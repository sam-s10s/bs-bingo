# Bullshit Bingo

Make changes and then update the `pcc-deploy.toml` file with the latest version.

### Deployment to Pipecat Cloud

```shell
# Enter server
cd server

# Build
uv run pipecat cloud docker build-push

# Deploy
uv run pipecat cloud deploy
```
