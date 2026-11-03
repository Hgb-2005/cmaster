const {test}=require('node:test');
const assert=require('node:assert/strict');
const {explain}=require('../static/js/feedback.js');

test('compiler guidance uses the first real GCC error and preserves its location',()=>{
  const result=explain({status:'CE',compile_msg:"main.c:4:3: error: expected ';' before 'return'\nmain.c:5:1: error: expected declaration"});
  assert.match(result.title,/第 4 行，第 3 列/);
  assert.match(result.text,/分号/);
  assert.match(explain({status:'CE',compile_msg:"main.c:2:5: error: 'foo' undeclared"}).text,/先声明后使用/);
});
test('wrong-answer hints compare using the same whitespace rules as the judge',()=>{
  const result=explain({status:'WA',cases:[{status:'AC'},{status:'WA',name:'负数',expected:'\n1  \r\n2\n',actual:'\r\n1\n3  \n\n'}]});
  assert.match(result.title,/第 2 行/);
  assert.match(result.text,/负数/);
  assert.match(explain({status:'WA',cases:[]}).title,/手算/);
});
test('environment failures and knowledge mistakes offer distinct next steps',()=>{
  assert.match(explain({status:'SE'}).text,/不会.*错题本/);
  assert.match(explain({status:'TLE'}).text,/循环变量/);
  assert.match(explain({status:'RE'}).text,/数组下标/);
  assert.match(explain({status:'AC'}).text,/边界输入/);
  assert.equal(explain({status:'OK'}),null);
});
