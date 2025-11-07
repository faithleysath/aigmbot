```bash
docker build -t ncatbot-app .
```

Create the network (if it doesn't exist):
```bash
docker network create lan1
```

Run the container:
```bash
touch config.yaml && docker run -it --rm --network lan1 -v "$(pwd)/src/main.py":/app/main.py -v "$(pwd)/src/plugins":/app/plugins -v "$(pwd)/config.yaml":/app/config.yaml -v "$(pwd)/data":/app/data --name ncatbot-container ncatbot-app
```

```bash
pip install ncatbot -U
```

```yaml
napcat:
  ws_uri: ws://192.168.97.2:3001
  ws_token: z3g:)+m?4CZM1XAb
  ws_listen_ip: 192.168.97.2
  webui_uri: http://192.168.97.2:6099
  webui_token: k)F-9G!A6G}+U{7J
  enable_webui: true
  check_napcat_update: false
  stop_napcat: false
  remote_mode: true
  report_self_message: false
  report_forward_message_detail: true
```
