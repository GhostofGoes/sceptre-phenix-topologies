#!/usr/bin/env node
// ReadProperty, ReadPropertyMultiple, and a unicast Who-Is with node-bacnet,
// the library FUXA's BACnet driver uses (FUXA 1.3.5 pins node-bacnet 0.2.4).
// Usage: node_bacnet_probe.js TARGET_IP [deviceId=3003]
// Run where node-bacnet resolves, e.g. in a FUXA container:
//   NODE_PATH=/usr/src/app/FUXA/server/_pkg/runtime/node_modules \
//     node node_bacnet_probe.js 10.20.60.13
// Verified at node-bacnet 0.2.4: callback style, readProperty(addr,
// {type,instance}, propertyId, cb) and readPropertyMultiple(addr,
// [{objectId, properties:[{id}]}], cb). 8 = DEVICE, 0 = ANALOG_INPUT,
// 77 = OBJECT_NAME, 85 = PRESENT_VALUE. FUXA polls with
// readPropertyMultiple(PRESENT_VALUE) and needs an iAm before it connects.
const Bacnet = require('node-bacnet');

const target = process.argv[2];
const dev = parseInt(process.argv[3] || '3003', 10);
if (!target) { console.error('usage: node_bacnet_probe.js TARGET_IP [deviceId]'); process.exit(2); }

const client = new Bacnet({ port: 47809, apduTimeout: 3000 }); // not 47808: FUXA may hold it
const timer = setTimeout(() => { console.log('FAIL: timeout'); client.close(); process.exit(1); }, 12000);
let failed = false;
const ok = (label, value) => console.log(`PASS ${label}:`, JSON.stringify(value));
const bad = (label, err) => { failed = true; console.log(`FAIL ${label}:`, (err && err.message) || err); };

client.on('iAm', (d) => ok('iAm (unicast whoIs)', { deviceId: d.payload.deviceId, from: d.header.sender }));

client.readProperty(target, { type: 8, instance: dev }, 77, (err, value) => {
  if (err) { bad('readProperty object-name', err); } else { ok('readProperty object-name', value.values); }
  const req = [{ objectId: { type: 0, instance: 0 }, properties: [{ id: 85 }] }];
  client.readPropertyMultiple(target, req, (err2, value2) => {
    if (err2) { bad('readPropertyMultiple AI0 presentValue', err2); } else { ok('readPropertyMultiple AI0 presentValue', value2.values); }
    client.whoIs({ address: target });
    setTimeout(() => {
      clearTimeout(timer);
      client.close();
      console.log(failed ? 'FAIL' : 'PASS');
      process.exit(failed ? 1 : 0);
    }, 2500);
  });
});
