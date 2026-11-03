(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.CMasterFeedback=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  function outputLines(text){
    const lines=String(text??'').replace(/\r\n?/g,'\n').split('\n').map(line=>line.trimEnd());
    while(lines.length&&!lines[0])lines.shift();
    while(lines.length&&!lines[lines.length-1])lines.pop();
    return lines;
  }
  function explain(result){
    const status=result.status;
    if(status==='CE'){
      const message=result.compile_msg||result.message||'';
      const first=message.match(/:(\d+):(\d+):\s*(?:fatal )?error:\s*([^\n]+)/);
      let tip='从第一条 error 开始改，后面的报错可能由它连带引起。';
      if(/expected.*[;；]/i.test(first?.[3]||message))tip='检查这一行和上一行结尾的分号，并确认括号已经配对。';
      else if(/undeclared/i.test(first?.[3]||message))tip='检查变量是否先声明后使用，拼写和大小写是否一致，声明是否在当前作用域。';
      else if(/expected.*[})]|end of input/i.test(first?.[3]||message))tip='从这一行向上检查圆括号和花括号是否成对，尤其是函数和 if / for 的结尾。';
      return {title:first?'先看第 '+first[1]+' 行，第 '+first[2]+' 列':'先解决第一条编译错误',text:tip+' 修改后再运行一次，原始错误保留在下方。'};
    }
    if(status==='WA'){
      const c=(result.cases||[]).find(c=>c.status==='WA');
      if(!c)return {title:'先用一个失败用例手算',text:'按题意写下中间结果，再对照输入、期望输出和实际输出，检查边界与格式。'};
      const expected=outputLines(c.expected);
      const actual=outputLines(c.actual);
      const index=Array.from({length:Math.max(expected.length,actual.length)},(_,i)=>i).find(i=>expected[i]!==actual[i]);
      return {title:index===undefined?'逐项核对这个失败用例':'忽略首尾空行后，第 '+(index+1)+' 行输出不同',text:'用「'+(c.name||'失败用例')+'」的输入手算一次；先检查运算过程和边界，再检查多余提示语、空格与小数位。'};
    }
    if(status==='TLE')return {title:'先检查循环是否能结束',text:'跟踪循环变量和退出条件；输入会不会一直读取？确认能结束后，再检查数据量下的算法复杂度。'};
    if(status==='RE')return {title:'先缩小到最小失败输入',text:'优先检查数组下标、空指针、除数是否为 0，再结合下方运行信息定位。'};
    if(status==='AC')return {title:'这道题通过了，再独立走一步',text:'自己补一个最小值或边界输入，解释为什么这段代码仍然正确，再试着不看答案复写核心思路。'};
    if(status==='SE')return {title:'本次是运行环境问题',text:'先检查首页的练习环境，再重试。这次失败不会作为知识错误加入错题本。'};
    return null;
  }
  return {explain};
});
