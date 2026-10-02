// Exercise the actual page renderer with a small, offline DOM fixture.
// No browser, credentials, network, API or microphone is used.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.children=[]; this.dataset={}; this.textContent=''; this.parent=null;
    this.classList={toggle(){}}; }
  append(...nodes) { for(const node of nodes) { this.detach(node);node.parent=this;this.children.push(node); } }
  prepend(node) { this.detach(node);node.parent=this;this.children.unshift(node); }
  detach(node) { if(node.parent)node.parent.children=node.parent.children.filter(x=>x!==node); }
  querySelector(selector) { return this.children.find(x=>x.className===selector.slice(1)); }
}
const html=fs.readFileSync(path.join(__dirname,'..','miko_voice.html'),'utf8');
const start=html.indexOf('  function turnContainer(');
const end=html.indexOf('  function systemLine(',start);
assert(start>0 && end>start);
const root=new Element();
const context={document:{createElement(){return new Element();}},transcriptEl:root,
  turnContainers:new Map(),itemTurns:new Map(),userLines:new Map(),assistantLines:new Map(),
  latestUserItemId:'u1'};
vm.createContext(context);
vm.runInContext(html.slice(start,end),context);
context.line('owner','u1','…',true,'u1'); // position reserved on speech_started
context.line('miko','a1','first reply',false,'u1'); // response beats final STT
context.line('owner','u2','next question',false,'u2');
context.line('miko','a2','next reply',false,'u2');
context.line('owner','u1','first question, transcribed late',false);
context.line('miko','a1','first reply',false,'u1'); // duplicate final report
assert.deepEqual(root.children.map(x=>x.dataset.turnId),['u1','u2']);
assert.deepEqual(root.children[0].children.map(x=>x.querySelector('.body').textContent),
  ['first question, transcribed late','first reply']);
assert.deepEqual(root.children[1].children.map(x=>x.querySelector('.body').textContent),
  ['next question','next reply']);
assert.equal(context.userLines.size,2);
assert.equal(context.assistantLines.size,2);
console.log('BROWSER_LATE_STT_ORDER_OK');

// Exercise the actual event handler too: placeholder text must not leak into
// live captions, and partial captions must reach the native mirror in order.
context.reportedCaptions=[];
context.latestAssistantItemId='';
context.responseItemIds=new Map();
context.responseTurns=new Map([['r1','u1']]);
context.interruptedResponseIds=new Set();
context.responseEpochs=new Map([['r1',0]]);
context.interruptionEpoch=0;
context.reportCriticalEvent=(type,fields)=>{context.reportedCaptions.push({type,...fields});return Promise.resolve(true);};
context.reportTranscript=()=>{};
const eventStart=html.indexOf('  function handleEvent(');
const eventEnd=html.indexOf('  async function start(',eventStart);
assert(eventStart>0 && eventEnd>eventStart);
vm.runInContext(html.slice(eventStart,eventEnd),context);
context.line('owner','u3','…',true,'u3');
context.userLines.get('u3').dataset.placeholder='1';
context.handleEvent({type:'conversation.item.input_audio_transcription.delta',item_id:'u3',delta:'שלום'});
context.handleEvent({type:'conversation.item.input_audio_transcription.delta',item_id:'u3',delta:' מיקו'});
assert.equal(context.userLines.get('u3').querySelector('.body').textContent,'שלום מיקו');
context.handleEvent({type:'response.output_audio_transcript.delta',item_id:'a3',response_id:'r1',delta:'היי'});
context.handleEvent({type:'response.output_audio_transcript.delta',item_id:'a3',response_id:'r1',delta:' אופק'});
assert.equal(context.assistantLines.get('a3').querySelector('.body').textContent,'היי אופק');
assert.deepEqual(context.reportedCaptions.map(e=>e.delta),['שלום',' מיקו','היי',' אופק']);
assert.equal(context.itemTurns.get('a3'),'u1');
console.log('BROWSER_STREAMED_CAPTIONS_OK');
