# 10. Team server

**What we will do:** run the web UI for a team.

## With a token
```sh
wireforge serve --workspace . --host 0.0.0.0 --port 8000 --token "$(openssl rand -hex 16)"
```
Open `http://<host>:8000/?token=<the token>`. Every request needs the token; without `--token` anyone who reaches the
port can change the models. For single sign-on, `--oidc-issuer` checks the tokens of your identity provider instead.

Several people can edit the same project: every save carries the version of the project it was based on, and a save
over a newer version is refused instead of overwriting someone else's work. The **Versions** tab compares the working
copy with the git history.

<!-- tutorial: hidden
wireforge serve --workspace . --port 8765 > serve.log 2>&1 &
server=$!
for attempt in $(seq 50); do python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')" 2>/dev/null && break; sleep 0.2; done
python -c "import json, urllib.request; print([p['id'] for p in json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/projects'))])"
kill $server
-->

## In Docker
The repository has a `Dockerfile` with WireForge, the web UI and the compilers:
```sh
docker build -t wireforge https://github.com/oguzhankynk/wireforge.git
docker run -p 8000:8000 -v "$PWD:/workspace" wireforge
```

The server is meant for a team's internal network. Read the deployment notes before you expose it.

**More:** [server and REST API](../SERVER.md), [deployment and operation](../SERVER.md#deployment-and-operation).

Next: [11. Documents and evidence](11_documents_and_evidence.md)
