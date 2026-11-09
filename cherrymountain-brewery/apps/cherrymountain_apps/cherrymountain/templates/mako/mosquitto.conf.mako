# Mosquitto on the historian, rendered by the cherrymountain app (DESIGN.md
# 12.4). Range-only: anonymous publishers on OPS (scada-fuxa) and the local
# Telegraf. fw-ot keeps every other zone away from tcp ${port}.
per_listener_settings true
persistence true
persistence_location /var/lib/mosquitto/

listener ${port} 127.0.0.1
allow_anonymous true

listener ${port} ${bind}
allow_anonymous true
