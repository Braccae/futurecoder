# Start with the Python 3.11.2 Alpine image
FROM python:3.11.2-alpine

COPY . /futurecoder
# Set the working directory to the futurecoder folder
WORKDIR /futurecoder

# Install poetry
RUN python3 -m pip install --user pipx
RUN pipx ensurepath
RUN sudo pipx ensurepath --global
RUN pipx install poetry
RUN poetry install

# generate various static files from Python used by the frontend and run some tests.
# Repeat this step whenever you change Python files.
RUN /futurecoder/scripts/generate.sh

# Change directory to frontend folder
WORKDIR /futurecoder/frontend

# Install Node.js (version 16.17.1 recommended)
RUN apk add --no-cache nodejs=16.17.1-r0

# Download dependencies
RUN npm ci

# Build the frontend
# Set environment variable for caching
ENV REACT_APP_PRECACHE=1
RUN npm run build

# Copy service-worker.js from course folder to public folder
COPY course/service-worker.js public/service-worker.js

# Start the frontend development server
EXPOSE 3000
CMD ["npm", "start"]