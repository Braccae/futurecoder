from __future__ import annotations

import ast
import inspect
import re
from abc import ABC, abstractmethod
from copy import deepcopy
from functools import partial
from importlib import import_module
from pathlib import Path
from textwrap import dedent, indent
from types import FunctionType
from typing import Type, Union, get_type_hints

from astcheck import is_ast_like
from asttokens import ASTTokens
from littleutils import setattrs, only
from markdown import markdown

from main.exercises import (
    check_exercise,
    check_result,
    generate_for_type,
    inputs_string,
)
from main.utils import no_weird_whitespace, snake, unwrapped_markdown


def get_solution_function(solution):
    if inspect.signature(solution).parameters:
        return solution
    else:
        return solution()


def clean_program(program, *, inputs=None):
    if isinstance(program, FunctionType):
        inputs = inputs_string(inputs or {})
        source = dedent(inspect.getsource(program))
        lines = source.splitlines()
        func = get_solution_function(program)
        if func != program:
            assert lines[0] == "def solution():"
            assert lines[-1] == f"    return {func.__name__}"
            source = dedent("\n".join(lines[1:-1]))
            program = clean_solution_function(func, source)
        else:
            atok = ASTTokens(source, parse=True)
            func = atok.tree.body[0]
            lines = lines[func.body[0].first_token.start[0] - 1:]
            program = inputs + '\n' + dedent('\n'.join(lines))
        compile(program, "<program>", "exec")  # check validity
    no_weird_whitespace(program)
    return program.strip()


def basic_signature(func, remove_first=False):
    param_names = list(inspect.signature(func).parameters.keys())
    if remove_first:
        param_names = param_names[1:]
    joined = ", ".join(param_names)
    return f'({joined})'


def clean_solution_function(func, source):
    return re.sub(
        f"(@returns_stdout\n)?"
        rf"def {func.__name__}\(_, .+?\):",
        rf"def {func.__name__}{basic_signature(func, remove_first=True)}:",
        source,
    )


def clean_step_class(cls, clean_inner=True):
    text = cls.text or cls.__doc__
    program = cls.program
    hints = cls.hints

    solution = cls.__dict__.get("solution", "")
    assert bool(solution) ^ bool(program)
    assert text
    no_weird_whitespace(text)

    if solution:
        assert cls.tests
        # noinspection PyUnresolvedReferences
        cls.solution = get_solution_function(solution)
        inputs = list(cls.test_values())[0][0]
        program = clean_program(solution, inputs=inputs)
    else:
        program = clean_program(program)
    assert program

    if isinstance(hints, str):
        hints = hints.strip().splitlines()
    hints = [markdown(hint) for hint in hints]

    if "__program_" in text:
        text = text.replace("__program__", program)
        indented = indent(program, '    ')
        text = re.sub(r" *__program_indented__", indented, text, flags=re.MULTILINE)
    else:
        assert not cls.program_in_text, "Either include __program__ or __program_indented__ in the text, " \
                                        "or set program_in_text = False in the class."

    assert "__program_" not in text

    text = markdown(dedent(text).strip())

    messages = []
    if clean_inner:
        for name, inner_cls in inspect.getmembers(cls):
            if not (isinstance(inner_cls, type) and issubclass(inner_cls, Step)):
                continue

            if issubclass(inner_cls, MessageStep):
                inner_cls.tests = inner_cls.tests or cls.tests
                clean_step_class(inner_cls)

                # noinspection PyAbstractClass
                class inner_cls(inner_cls, cls):
                    __name__ = inner_cls.__name__
                    __qualname__ = inner_cls.__qualname__
                    __module__ = inner_cls.__module__
                    program_in_text = inner_cls.program_in_text

                messages.append(inner_cls)

                if inner_cls.after_success and issubclass(inner_cls, ExerciseStep):
                    check_exercise(
                        partial(inner_cls.solution, None),
                        partial(cls.solution, None),
                        cls.test_exercise,
                        cls.generate_inputs,
                    )

            clean_step_class(inner_cls, clean_inner=False)

    setattrs(cls,
             text=text,
             program=program,
             messages=messages,
             hints=hints)


pages = {}
page_slugs_list = []


class PageMeta(type):
    final_text = None
    step_names = []
    step_texts = []

    def __init__(cls, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if cls.__name__ == "Page":
            return
        pages[cls.slug] = cls
        page_slugs_list.append(cls.slug)
        cls.step_names = []
        cls.step_texts = []
        for key, value in cls.__dict__.items():
            if getattr(value, "is_step", False):
                clean_step_class(value)
                cls.step_names.append(key)
                cls.step_texts.append(value.text)

        assert isinstance(cls.final_text, str)
        no_weird_whitespace(cls.final_text)
        cls.final_text = markdown(cls.final_text.strip())
        cls.step_names.append("final_text")
        cls.step_texts.append(cls.final_text)

    @property
    def slug(cls):
        return cls.__dict__.get("slug", cls.__name__)

    @property
    def title(cls):
        return unwrapped_markdown(cls.__dict__.get(
            "title",
            snake(cls.slug)
                .replace("_", " ")
                .title()
        ))

    @property
    def index(self):
        return page_slugs_list.index(self.slug)

    @property
    def next_page(self):
        return pages[page_slugs_list[self.index + 1]]

    @property
    def previous_page(self):
        return pages[page_slugs_list[self.index - 1]]

    @property
    def steps(self):
        return [getattr(self, step_name) for step_name in self.step_names]

    @property
    def step_dicts(self):
        return [
            dict(
                text=text,
                name=name,
                hints=getattr(step, "hints", []),
            )
            for name, text, step in
            zip(self.step_names, self.step_texts, self.steps)
        ]


class Page(metaclass=PageMeta):
    @classmethod
    def check_step(cls, code_entry, output, console):
        step_cls: Type[Step] = getattr(cls, code_entry['step_name'])
        step = step_cls(code_entry['input'], output, code_entry['source'], console)
        try:
            return step.check_with_messages()
        except SyntaxError:
            return False

    # Workaround for Django templates which can't see metaclass properties
    @classmethod
    def title_prop(cls):
        return cls.title

    @classmethod
    def slug_prop(cls):
        return cls.slug

    @classmethod
    def index_prop(cls):
        return cls.index


class Step(ABC):
    text = ""
    program = ""
    program_in_text = False
    hints = ()
    is_step = True
    messages = ()
    tests = {}
    expected_code_source = None

    def __init__(self, *args):
        self.args = args
        self.input, self.result, self.code_source, self.console = args

    def check_with_messages(self):
        if self.expected_code_source not in (None, self.code_source):
            return False

        result = self.check()
        if not isinstance(result, dict):
            result = bool(result)
        for message_cls in self.messages:
            if result == message_cls.after_success and message_cls.check_message(self):
                return message_cls.message()
        return result

    @abstractmethod
    def check(self) -> Union[bool, dict]:
        raise NotImplementedError

    @property
    def tree(self):
        return ast.parse(self.input)

    @property
    def stmt(self):
        return self.tree.body[0]

    def tree_matches(self, template):
        if is_ast_like(self.tree, ast.parse(template)):
            return True

        if is_ast_like(ast.parse(self.input.lower()), ast.parse(template.lower())):
            return dict(
                message="Python is case sensitive! That means that small and capital letters "
                        "matter and changing them changes the meaning of the program. The strings "
                        "`'hello'` and `'Hello'` are different, as are the variable names "
                        "`word` and `Word`."
            )

    def matches_program(self):
        return self.tree_matches(self.program)

    def input_matches(self, pattern, remove_spaces=True):
        inp = self.input.rstrip()
        if remove_spaces:
            inp = re.sub(r'\s', '', inp)
        return re.match(pattern + '$', inp)

    @property
    def function_tree(self):
        # We define this here so MessageSteps implicitly inheriting from ExerciseStep don't complain it doesn't exist
        # noinspection PyUnresolvedReferences
        function_name = self.solution.__name__

        if function_name == "solution":
            raise ValueError("This exercise doesn't require defining a function")

        return only(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
            if node.name == function_name
        )


class ExerciseStep(Step):
    def check(self):
        if self.code_source == "shell":
            return False

        function_name = self.solution.__name__

        if function_name == "solution":
            return check_exercise(
                self.input,
                self.solution,
                self.test_exercise,
                self.generate_inputs,
                functionise=True,
            )
        else:
            if function_name not in self.console.locals:
                return dict(message=f"You must define a function `{function_name}`")

            func = self.console.locals[function_name]
            if not inspect.isfunction(func):
                return dict(message=f"`{function_name}` is not a function.")

            actual_signature = basic_signature(func)
            needed_signature = basic_signature(self.solution)
            if actual_signature != needed_signature:
                return dict(
                    message=f"The signature should be:\n\n"
                            f"    def {function_name}{needed_signature}:\n\n"
                            f"not:\n\n"
                            f"    def {function_name}{actual_signature}:"
                )

            return check_exercise(
                func,
                self.solution,
                self.test_exercise,
                self.generate_inputs,
            )

    @abstractmethod
    def solution(self, *args, **kwargs):
        raise NotImplementedError

    @classmethod
    def arg_names(cls):
        return list(inspect.signature(cls.solution).parameters)[1:]

    @classmethod
    def test_values(cls):
        tests = cls.tests
        if isinstance(tests, dict):
            tests = tests.items()
        for inputs, result in tests:
            if not isinstance(inputs, dict):
                if not isinstance(inputs, tuple):
                    inputs = (inputs,)
                arg_names = cls.arg_names()
                assert len(arg_names) == len(inputs)
                inputs = dict(zip(arg_names, inputs))
            inputs = deepcopy(inputs)
            yield inputs, result

    @classmethod
    def test_exercise(cls, func):
        for inputs, result in cls.test_values():
            check_result(func, inputs, result)

    @classmethod
    def generate_inputs(cls):
        return {
            name: generate_for_type(typ)
            for name, typ in get_type_hints(cls.solution).items()
        }


class VerbatimStep(Step):
    program_in_text = True

    def check(self):
        return self.matches_program()


class MessageStep(Step, ABC):
    after_success = False

    @classmethod
    def message(cls):
        return dict(message=cls.text)

    @classmethod
    def check_message(cls, step):
        return cls(*step.args).check()


def search_ast(node, template):
    return any(
        is_ast_like(child, template)
        for child in ast.walk(node)
    )


def load_chapters():
    chapters_dir = Path(__file__).parent / "chapters"
    path: Path
    for path in sorted(chapters_dir.glob("c*.py")):
        module_name = path.stem
        full_module_name = "main.chapters." + module_name
        module = import_module(full_module_name)
        title = module_name[4:].replace("_", " ").title()
        chapter_pages = [p for p in pages.values() if p.__module__ == full_module_name]
        yield title, module, chapter_pages


chapters = list(load_chapters())
