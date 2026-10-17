# WireForge team server: REST API, web UI and the command line, with the C++ toolchain needed to
# build and run generated tests.
#   docker build -t wireforge .
#   docker run -p 8000:8000 -v "$PWD/projects:/workspace" wireforge
# The default command has no token; see docs/SERVER.md, "Deployment and operation".
FROM node:22-bookworm-slim AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npx vite build

FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ cmake ninja-build git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml setup.py README.md ./
COPY codegen/ codegen/
COPY schema/ schema/
COPY runtime/ runtime/
COPY cmake/ cmake/
COPY --from=ui /src/ui/dist ui/dist
RUN pip install --no-cache-dir ".[server,mcp,import]" && rm -rf /src
COPY examples/ /opt/wireforge/examples/
RUN useradd --create-home wireforge && mkdir /workspace && chown wireforge /workspace
USER wireforge
VOLUME /workspace
EXPOSE 8000
CMD ["wireforge", "serve", "--workspace", "/workspace", "--host", "0.0.0.0", "--port", "8000"]
