// Unit tests for the dashboard's pure functions, run under gjs (SpiderMonkey).
//
//     gjs tests/test_app.js
//
// The file is loaded with just enough DOM stubbed out for it to evaluate, then
// the functions worth testing are handed back.

const GLib = imports.gi.GLib;
const [, bytes] = GLib.file_get_contents('monitor/web/app.js');
const src = new TextDecoder().decode(bytes);

const fakeEl = () => new Proxy({}, {
    get(target, prop) {
        if (prop === 'querySelectorAll') return () => [];
        if (prop === 'closest') return () => null;
        if (prop === 'getBoundingClientRect') return () => ({ left: 0, top: 0, width: 100, height: 50 });
        if (prop === 'appendChild') return x => x;
        if (prop === 'addEventListener') return () => {};
        if (prop === 'setAttribute') return () => {};
        if (prop === 'clientWidth') return 600;
        if (prop === 'clientHeight') return 170;
        if (prop in target) return target[prop];
        return fakeEl();
    },
    set(target, prop, value) { target[prop] = value; return true; },
});

const harness = `
    const document = {
        getElementById: () => (${fakeEl})(),
        createElementNS: () => (${fakeEl})(),
        createElement: () => (${fakeEl})(),
        documentElement: {},
    };
    const window = { addEventListener: () => {}, innerWidth: 1200, innerHeight: 800 };
    const getComputedStyle = () => ({ getPropertyValue: () => '#abcdef' });
    const fetch = () => Promise.reject(new Error('no network in tests'));
    const setInterval = () => 0, setTimeout = () => 0, clearTimeout = () => {};
    ${src}
    return { scaleFor, bps, bytes, pct, temp, duration, coreLines, tempMeter, drawChart, CHART_DEFS };
`;

const api = new Function(harness)();
let failures = 0;
const check = (name, actual, expected) => {
    const a = JSON.stringify(actual), e = JSON.stringify(expected);
    const ok = a === e;
    if (!ok) failures++;
    print(`  ${ok ? 'ok  ' : 'FAIL'} ${name}${ok ? '' : `\n         expected ${e}\n         got      ${a}`}`);
};

print('scaleFor:');
check('percentages stay pinned to 0-100', api.scaleFor([0, 50, 100], { fixed: [0, 100] }).ticks, [0, 25, 50, 75, 100]);
const t1 = api.scaleFor([30, 45], {});
check('temperatures snap to steps of 5', [t1.lo, t1.hi, t1.ticks.length], [30, 45, 4]);
const t2 = api.scaleFor([1934.98, 36543.72], { floor: 0 });
check('network starts at zero', t2.lo, 0);
check('network covers the peak', t2.hi >= 36543.72, true);
check('network keeps at most 6 ticks', t2.ticks.length <= 6, true);
const t3 = api.scaleFor([], {});
check('an empty series does not break', [t3.lo, t3.hi], [0, 1]);
const t4 = api.scaleFor([null, null, undefined], {});
check('an all-null series does not break', [t4.lo, t4.hi], [0, 1]);
const t5 = api.scaleFor([42, 42, 42], {});
check('a flat series still spans a range', t5.hi > t5.lo, true);

print('\nformatters:');
check('bps in bytes', api.bps(140), '140 B/s');
check('bps rounds into kB', api.bps(36543), '37 kB/s');
check('bps in MB', api.bps(125000000), '125 MB/s');
check('bps of null', api.bps(null), '—');
check('bytes in GB', api.bytes(16180760576), '15.1 GB');
check('pct keeps a decimal below 10', api.pct(4.25), '4.3%');
check('pct drops decimals above 10', api.pct(25.8), '26%');
check('temp', api.temp(42), '42°C');
check('duration in days', api.duration(178441), '2d 1h');
check('duration in minutes', api.duration(900), '15m');
check('tempMeter maps 20°C to 0%', api.tempMeter(20), 0);
check('tempMeter maps 100°C to 100%', api.tempMeter(100), 100);

print('\nmalformed API payloads:');
// The API answers errors as {"error": ...}. Rendering one must not throw, or
// the dashboard would freeze instead of reporting that it is disconnected.
const errorPayload = { error: 'internal server error' };
const tryRender = (name, data, lines) => {
    const plot = fakeEl();
    try {
        api.drawChart(plot, api.CHART_DEFS[0], data, lines);
        check(name, true, true);
    } catch (e) {
        check(name, `threw: ${e}`, true);
    }
};
tryRender('an error payload renders instead of throwing', errorPayload, [{ name: 'CPU', color: '--cpu', data: [] }]);
tryRender('a null payload renders instead of throwing', null, [{ name: 'CPU', color: '--cpu', data: [] }]);
tryRender('a payload with no series renders', { t: [1, 2] }, []);
check('coreLines ignores an error payload', api.coreLines(errorPayload), []);

print('\ncoreLines:');
const cl = api.coreLines({ cores: { 'Core 1': [1, 2], 'Core 0': [3, 4] } });
check('cores come out sorted', cl.map(l => l.name), ['Core 0', 'Core 1']);
check('each core gets its own colour', cl[0].color !== cl[1].color, true);
check('no data yields no lines', api.coreLines(null), []);

print(`\n${failures === 0 ? 'ALL TESTS PASSED' : failures + ' FAILURE(S)'}`);
if (failures) imports.system.exit(1);
