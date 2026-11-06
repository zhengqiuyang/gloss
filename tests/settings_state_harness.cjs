// Execute the actual embedded page script; no browser/DOM package required.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {script, scenario} = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = new Map();
class Element {
  constructor() {
    this.value = ''; this.dataset = {}; this.style = {display:'none'};
    this.checked = false; this.textContent = ''; this.handlers = {};
    this.attrs = {}; this.children = []; this.placeholder = '';
  }
  set id(v) { this._id = v; nodes.set(v, this); }
  get id() { return this._id; }
  appendChild(n) { this.children.push(n); return n; }
  addEventListener(event, fn) { this.handlers[event] = fn; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  focus() {}
  setSelectionRange() {}
}
const document = {
  getElementById(id) {
    if (!nodes.has(id)) { const node = new Element(); node.id = id; }
    return nodes.get(id);
  },
  createElement: () => new Element(),
  addEventListener: () => {},
};
let cfg = {base_url:'http://example.invalid/v1', model:'test-model', vision_model:'',
           api_key:'', hotkey:'cmd+g', language:'中文', auto_lookup:false, has_api_key:false};
let savedKey = '';
let posts = 0;
let gate = null;
let failNext = false;
const clone = x => JSON.parse(JSON.stringify(x));
function view() {
  return {...cfg, api_key:savedKey ? 'test…only' : '', has_api_key:!!savedKey};
}
async function fetch(url, options = {}) {
  if (url !== '/api/config') throw Error('unexpected request ' + url);
  if (!options.method) return {json: async () => view()};
  ++posts;
  const payload = JSON.parse(options.body);
  if (gate) await gate;
  if (failNext) { failNext = false; return {json:async () => ({error:'test save rejected'})}; }
  for (const [key,value] of Object.entries(payload)) {
    if (key === 'api_key') savedKey = value;
    else cfg[key] = value;
  }
  return {json:async () => ({ok:true, config:view()})};
}
const nativeMessages = [];
const context = vm.createContext({document, fetch, console, window:{close(){},
  webkit:{messageHandlers:{glossSettings:{postMessage(open){nativeMessages.push(open);}}}}}});
vm.runInContext(script, context);
const field = key => nodes.get('set-' + key);
const click = id => nodes.get(id).handlers.click();
function delaySave() {
  let release;
  gate = new Promise(resolve => { release = () => { gate = null; resolve(); }; });
  return release;
}
(async () => {
  await click('cfgbtn');
  if (scenario === 'native-settings') {
    assert.deepEqual(nativeMessages, [true]);
    field('api_key').value = 'unsaved-test-key';
    assert.equal(nodes.get('settings').style.display, '');
    await click('sclose');
    assert.deepEqual(nativeMessages, [true, false]);
    await click('cfgbtn'); await click('cfgbtn');
    assert.deepEqual(nativeMessages, [true, false, true, false]);
  } else if (scenario === 'serial') {
    field('vision_model').value = 'X'; await click('ssave');
    assert.equal(field('vision_model').dataset.pristine, 'X');
    field('vision_model').value = ''; await click('ssave');
    assert.equal(cfg.vision_model, '');
    field('language').value = 'English'; await click('ssave');
    field('language').value = '中文'; await click('ssave');
    assert.equal(cfg.language, '中文');
  } else if (scenario === 'pending-edit') {
    const release = delaySave();
    field('vision_model').value = 'X'; const pending = click('ssave');
    field('vision_model').value = ''; release(); await pending;
    assert.equal(field('vision_model').value, '');
    assert.equal(field('vision_model').dataset.pristine, 'X');
    assert.match(nodes.get('smsg').textContent, /未保存修改/);
    await click('ssave'); assert.equal(cfg.vision_model, '');
  } else if (scenario === 'reopened' || scenario === 'reopened-clear') {
    const release = delaySave();
    field('vision_model').value = 'X'; const pending = click('ssave');
    await click('sclose'); await click('cfgbtn');
    const draft = scenario === 'reopened-clear' ? '' : 'Y';
    field('vision_model').value = draft; release(); await pending;
    assert.equal(field('vision_model').value, draft);
    await click('ssave'); assert.equal(cfg.vision_model, draft);
  } else if (scenario === 'failure') {
    failNext = true; field('vision_model').value = 'X'; await click('ssave');
    assert.equal(cfg.vision_model, '');
    assert.equal(field('vision_model').dataset.pristine, '');
    assert.equal(field('vision_model').value, 'X');
    await click('ssave'); assert.equal(cfg.vision_model, 'X');
  } else if (scenario === 'duplicate') {
    const release = delaySave();
    field('vision_model').value = 'X'; const pending = click('ssave');
    await click('ssave'); assert.equal(posts, 1); release(); await pending;
    assert.equal(cfg.vision_model, 'X');
  } else if (scenario === 'key') {
    await click('ssave');
    assert.equal(vm.runInContext('NO_KEY', context), true);
    field('api_key').value = 'test-only-not-a-real-key'; await click('ssave');
    assert.equal(field('api_key').value, '');
    assert.equal(field('api_key').placeholder, 'test…only');
    assert.equal(vm.runInContext('NO_KEY', context), false);
  } else { throw Error('unknown scenario'); }
  console.log('PASS settings state:', scenario);
})().catch(error => { console.error(error); process.exitCode = 1; });
