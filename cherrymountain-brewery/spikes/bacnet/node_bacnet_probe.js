#!/usr/bin/env node
// ReadProperty with node-bacnet, the library FUXA's BACnet driver uses.
// Run on the FUXA VM: cd /opt/fuxa/server && node /path/node_bacnet_probe.js 10.20.60.13
// Usage: node_bacnet_probe.js TARGET_IP [deviceId=3003]
// TODO(verify): node-bacnet API (v0.2.x callback style assumed); check the
// installed version's README. 8 = object type DEVICE, 77 = OBJECT_NAME.
const Bacnet = require('node-bacnet');

const target = process.argv[2];
const dev = parseInt(process.argv[3] || '3003', 10);
if (!target) { console.error('usage: node_bacnet_probe.js TARGET_IP [deviceId]'); process.exit(2); }

const client = new Bacnet({ port: 47809, apduTimeout: 3000 }); // not 47808: FUXA may hold it
const timer = setTimeout(() => { console.log('FAIL: timeout'); client.close(); process.exit(1); }, 8000);

client.readProperty(target, { type: 8, instance: dev }, 77, (err, value) => {
  clearTimeout(timer);
  client.close();
  if (err) { console.log('FAIL:', err.message || err); process.exit(1); }
  console.log('PASS:', JSON.stringify(value));
  process.exit(0);
});
