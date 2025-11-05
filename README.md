```bash
docker build --no-cache -t ncatbot-app .
```

```bash
touch config.yaml && docker run -it --rm -v "$(pwd)/src/main.py":/app/main.py -v "$(pwd)/src/plugins":/app/plugins -v "$(pwd)/config.yaml":/app/config.yaml --name ncatbot-container ncatbot-app
```

```bash
pip install ncatbot -U
```