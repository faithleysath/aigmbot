```bash
docker build -t ncatbot-app .
```

```bash
touch config.yaml && docker run -it --rm -e BOT_QQ="{}" -e ROOT_QQ="{}" -v "$(pwd)/src/main.py":/app/main.py -v "$(pwd)/src/plugins":/app/plugins -v "$(pwd)/config.yaml":/app/config.yaml --name ncatbot-container ncatbot-app
```