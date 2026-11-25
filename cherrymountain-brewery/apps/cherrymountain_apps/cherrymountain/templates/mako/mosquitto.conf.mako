# Mosquitto on the historian, rendered by the cherrymountain app (DESIGN.md
# 12.4). Range-only: anonymous publishers on OPS (scada-fuxa) and the local
# Telegraf. fw-ot keeps every other zone away from tcp ${port}.
per_listener_settings true
persistence true
persistence_location /var/lib/mosquitto/

# One listener on every address. A listener bound to the node's own address
# (${bind}) makes Mosquitto exit when it starts before phenix has set that address,
# and after five such starts systemd gives up on the unit: no broker, and no
# Telegraf, for the rest of the run.
listener ${port}
allow_anonymous true
