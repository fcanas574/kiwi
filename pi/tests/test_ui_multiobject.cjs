const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const filename=path.join(__dirname,'../kiwi_pi/static/multiobject.js');
function helpers(){
  assert.ok(fs.existsSync(filename),'Multiobject UI helpers must exist');
  const context={module:{exports:{}}}; vm.runInNewContext(fs.readFileSync(filename,'utf8'),context);
  return context.module.exports;
}
test('boxes remain aligned at any displayed size',()=>{
  assert.deepEqual({...helpers().boxStyle([20,10,60,30],[100,50])},{left:'20%',top:'20%',width:'40%',height:'40%'});
});
test('partial confirmation keeps other locations pending',()=>{
  const result={operation_id:'a',slots:[{id:1},{id:7}]};
  const status={guide:{operation_id:'a',phase:'guiding',pending:[7],confirmed:[1],wrong_slots:[],blocked:[]}};
  const progress=helpers().guideProgress(result,status);
  assert.equal(progress.total,2); assert.equal(progress.confirmed,1); assert.equal(progress.complete,false);
});
test('events from another photo do not update equal-category results',()=>{
  const result={operation_id:'new',slots:[{id:1}]};
  const status={guide:{operation_id:'old',phase:'returned',confirmed:[1]}};
  assert.equal(helpers().guideProgress(result,status),null);
});
test('progress counts locations rather than duplicate detections',()=>{
  const result={operation_id:'a',objects:[{id:1},{id:2}],slots:[{id:7}]};
  const status={guide:{operation_id:'a',phase:'returned',confirmed:[7],pending:[],wrong_slots:[],blocked:[]}};
  const progress=helpers().guideProgress(result,status);
  assert.equal(progress.total,1); assert.equal(progress.complete,true);
});
