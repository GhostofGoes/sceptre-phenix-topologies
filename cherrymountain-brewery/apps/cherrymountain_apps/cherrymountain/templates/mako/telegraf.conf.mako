# Telegraf on the historian, rendered by the cherrymountain app (DESIGN.md
# 12.4): PackTags/OEE from Mosquitto plus host metrics, into InfluxDB 2.
# INFLUX_TOKEN comes from /etc/default/cm-historian through a systemd drop-in.

[agent]
  interval = "10s"
  flush_interval = "10s"
  round_interval = true

[[outputs.influxdb_v2]]
  urls = ["http://127.0.0.1:${influx_port}"]
  token = "<%text>${INFLUX_TOKEN}</%text>"
  organization = "${org}"
  bucket = "${bucket}"

# PackTags published by the FUXA server script on ISA-95 topics, for example
# ${topic_root}/packaging/bottling-line/packtags/<tag>.
[[inputs.mqtt_consumer]]
  servers = ["tcp://127.0.0.1:${mqtt_port}"]
  topics = ["${topic_root}/+/+/packtags/#"]
  qos = 0
  client_id = "cm-telegraf"
  data_format = "value"
  data_type = "float"
  name_override = "packtags"
  topic_tag = "topic"

[[inputs.cpu]]
  percpu = false
  totalcpu = true

[[inputs.mem]]

[[inputs.disk]]
  ignore_fs = ["tmpfs", "devtmpfs", "overlay", "squashfs"]

[[inputs.system]]
