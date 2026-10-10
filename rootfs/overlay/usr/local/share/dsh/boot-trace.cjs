'use strict';
// DSHIOS boot trace -- stderr-only diagnostics for the web surface.
//
// Loaded with --require from /usr/local/bin/dsh-serve. Why it exists: on device
// the web UI occasionally never gets its server (the WKWebView waits forever),
// while the same tree answers in seconds on a desktop. This app's Server Log is
// the only channel that shows what the guest process is doing, so this file
// prints -- to stderr only --
//
//   * a banner with node version, pid, home/cwd, and the harness argv;
//   * one line per HTTP listen() call, so the log shows whether the server ever
//     reached the point of binding a port;
//   * a cpu/rss/heap/handles snapshot every 20 s for the first 10 minutes, so a
//     stall reads as either "busy crunching" (cpu climbing) or "blocked" (cpu
//     flat, handles stuck).
//
// Every hook is wrapped; a trace failure must never be able to take the real
// process down. Nothing is written to stdout (it belongs to the harness).
(function () {
  var started = Date.now();
  function stamp() { return 't=' + Math.round((Date.now() - started) / 1000) + 's'; }
  function say(line) {
    try { process.stderr.write('[boot-trace] ' + stamp() + ' ' + line + '\n'); } catch (e) {}
  }

  try {
    say('node ' + process.version + ' pid=' + process.pid +
        ' home=' + (process.env.DSH_HOME || '?') +
        ' cwd=' + process.cwd() +
        ' argv=' + JSON.stringify(process.argv.slice(1, 4)));
  } catch (e) {}

  try {
    var net = require('node:net');
    var listen = net.Server.prototype.listen;
    net.Server.prototype.listen = function () {
      var args = Array.prototype.slice.call(arguments).filter(function (a) {
        return typeof a !== 'function';
      });
      say('listen ' + JSON.stringify(args));
      return listen.apply(this, arguments);
    };
  } catch (e) { say('net hook failed: ' + e.message); }

  var n = 0;
  var timer = setInterval(function () {
    n += 1;
    try {
      var cpu = process.cpuUsage();
      var mem = process.memoryUsage();
      var handles = -1;
      try { handles = process._getActiveHandles().length; } catch (e) {}
      say('cpu=' + Math.round((cpu.user + cpu.system) / 1000) + 'ms' +
          ' rss=' + Math.round(mem.rss / 1048576) + 'MB' +
          ' heap=' + Math.round(mem.heapUsed / 1048576) + 'MB' +
          ' handles=' + handles);
    } catch (e) {}
    if (n >= 30) clearInterval(timer);
  }, 20000);
  if (timer.unref) timer.unref();
})();
