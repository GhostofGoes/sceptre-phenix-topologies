// The S7 server of the stand-in plant (plc-packaging), in a process of its own:
// node-snap7's server crashes when it shares a process with the other servers.
//
//   node fuxa_s7.cjs PKG_DIR     (forked by fuxa_plant.mjs, which talks to it by IPC)
//
// node-snap7's server copies a registered buffer, so later changes to the
// buffer are not seen. This process therefore keeps its own image of each DB
// and writes the image to its own server over S7 (DBWrite), as a PLC program
// would. A value FUXA writes to this server is overwritten by the next one.
//
// Messages from the parent: {type: 'layout', dbs: {<db>: <size>}} once, then
// {type: 'values', items: [[db, byte, bit, isWord, raw], ...]} to set values.
// Message to the parent: {type: 'started', ok}.
const snap7 = require(process.argv[2] + '/node_modules/node-snap7');

const server = new snap7.S7Server();
const client = new snap7.S7Client();
const images = new Map(); // db -> Buffer
let connected = false;
let busy = false;

// One S7 call at a time on the client.
const call = (fn) => new Promise((resolve) => fn((err) => resolve(err)));

async function flush() {
  if (!connected || busy) { return; }
  busy = true;
  try {
    for (const [db, image] of images) { await call((cb) => client.DBWrite(db, 0, image.length, image, cb)); }
  } finally {
    busy = false;
  }
}

process.on('message', (msg) => {
  if (msg.type === 'layout') {
    for (const [db, size] of Object.entries(msg.dbs)) {
      const image = Buffer.alloc(Math.max(size + 2, 1024));
      images.set(Number(db), image);
      server.RegisterArea(server.srvAreaDB, Number(db), Buffer.alloc(image.length));
    }
    const ok = server.StartTo('127.0.0.1');
    client.ConnectTo('127.0.0.1', 0, 1, (err) => { connected = !err; });
    process.send({ type: 'started', ok });
  } else if (msg.type === 'values') {
    for (const [db, byte, bit, word, raw] of msg.items) {
      const image = images.get(db);
      if (word) { image.writeUInt16BE(raw & 0xffff, byte); } else {
        image[byte] = raw ? (image[byte] | (1 << bit)) : (image[byte] & ~(1 << bit));
      }
    }
    flush();
  }
});
