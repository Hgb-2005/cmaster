(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.CMasterFeedback=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';

  function outputLines(text){
    const lines=String(text??'').replace(/\r\n?/g,'\n').split('\n').map(line=>line.trimEnd());
    while(lines.length&&!lines[0])lines.shift();
    while(lines.length&&!lines[lines.length-1])lines.pop();
    return lines;
  }

  function failedCase(result){
    return (result.cases||[]).find(c=>c.status!=='AC')||(result.cases||[])[0]||null;
  }

  function clip(text,limit){
    const value=String(text||'').replace(/\s+/g,' ').trim();
    return value.length>limit?value.slice(0,limit-1)+'…':value;
  }

  function compileCoach(result){
    const message=result.compile_msg||result.message||'';
    const first=message.match(/:(\d+):(\d+):\s*(?:fatal )?error:\s*([^\n]+)/);
    const line=first?first[1]:'';
    const column=first?first[2]:'';
    const detail=first?first[3]:clip(message,180);
    let concept='编译器无法把当前源码翻译成可执行程序';
    let clue='从第一条 error 开始改，后面的报错经常只是它的连锁反应。';
    if(/expected.*[;；]/i.test(detail)){concept='语句结尾或括号边界不完整';clue='检查报错行和上一行结尾的分号，并确认圆括号、花括号已经配对。';}
    else if(/undeclared|not declared/i.test(detail)){concept='变量、函数或头文件违反了“先声明后使用”';clue='核对标识符的拼写和大小写；确认声明在当前位置可见，所需头文件也已包含。';}
    else if(/expected.*[})]|unterminated|end of input|missing terminating/i.test(detail)){concept='代码块或字符串没有正确闭合';clue='从报错位置向上找最近的圆括号、花括号或双引号，先补齐成对结构。';}
    else if(/no such file|fatal error.*include/i.test(detail)){concept='预处理器找不到指定头文件';clue='核对 #include 的拼写与尖括号/引号写法，确认这个头文件在当前编译环境可用。';}
    else if(/conflicting types|too few arguments|too many arguments/i.test(detail)){concept='函数声明、定义和调用方式不一致';clue='对照函数的参数类型与数量，再逐处核对函数名拼写和返回类型。';}
    return {
      status:'CE',
      title:'编译定位',
      steps:[
        {label:'观察',title:line?'第一条错误在第 '+line+' 行，第 '+column+' 列':'先处理编译器输出的第一条错误',body:detail?'编译器说：“'+clip(detail,150)+'”':'下方保留了完整编译器输出。'},
        {label:'判断',title:concept,body:'这类错误发生在运行之前，当前程序还没有开始执行。先只处理最靠前的一条诊断。'},
        {label:'线索',title:'改一处，再立即重编',body:clue+'不要一次根据所有报错同时大改。'}
      ]
    };
  }

  function wrongAnswerCoach(result){
    const c=failedCase(result);
    if(!c)return {
      status:'WA',
      title:'先手算，再对照输出',
      steps:[
        {label:'观察',title:'先手算：程序结束，但没有完全匹配答案',body:'逐项比较输入、期望输出和实际输出，先找第一个真正不同的位置。'},
        {label:'判断',title:'可能是计算、分支或输出格式之一',body:'把“我算出的值”和“我打印出来的格式”分开检查，避免只盯算法却漏掉空格、标点和小数位。'},
        {label:'线索',title:'用一个最小输入手算',body:'选能让流程走到当前分支的最小输入，在纸上记录每一步变量值，再和程序输出对齐。'}
      ]
    };
    const expected=outputLines(c.expected);
    const actual=outputLines(c.actual);
    const length=Math.max(expected.length,actual.length);
    let index=-1;
    for(let i=0;i<length;i++){if(expected[i]!==actual[i]){index=i;break;}}
    const expectedLine=expected[index]??'（缺少这一行）';
    const actualLine=actual[index]??'（没有输出）';
    const compactExpected=expectedLine.replace(/\s+/g,'');
    const compactActual=actualLine.replace(/\s+/g,'');
    const formatting=index>=0&&compactExpected===compactActual&&expectedLine!==actualLine;
    const lineCountMismatch=expected.length!==actual.length&&(index<0||index>=Math.min(expected.length,actual.length));
    let concept='计算过程、分支选择或边界处理可能不一致';
    let clue='用失败用例的输入手算一遍，重点核对条件判断、循环终点和变量更新的先后顺序。';
    if(formatting){concept='数值内容看起来相同，差异集中在空格或排版';clue='对照样例检查连续空格、行首缩进、换行数量和最后一行是否需要换行；判题会忽略行尾空白，但不会自动修正中间空格。';}
    else if(lineCountMismatch){concept='输出的行数与题目约定不一致';clue='数一数程序循环执行了多少次，并确认每次迭代是否都应该产生一行输出；同时检查末尾是否多打印了内容。';}
    const where=index>=0?'第 '+(index+1)+' 行输出不同':'输出行数与期望不同';
    return {
      status:'WA',
      title:'错例追踪',
      steps:[
        {label:'观察',title:(c.name||'失败用例')+'：'+where,body:'实际输出是“'+clip(actualLine,100)+'”，但程序正常结束了，所以先查逻辑，不用先怀疑运行环境。'},
        {label:'判断',title:concept,body:formatting?'期望行与实际行去掉空格后相同，问题更可能出在输出格式。':'这是一次确定性结果偏差；同一份代码处理相同输入时，会稳定走到错误分支。'},
        {label:'线索',title:'在这个用例上做最小化跟踪',body:clue}
      ]
    };
  }

  function runtimeCoach(result){
    const c=failedCase(result);
    const message=(c&&c.stderr)||result.stderr||result.message||'运行信息为空';
    const first=clip(message.split(/\r?\n/).find(Boolean)||message,160);
    const joined=String(message);
    let concept='程序访问了无效资源，或在运行时主动异常退出';
    let clue='先检查失败用例里的数组下标、指针生命周期和除数；再用最小输入缩小到具体语句。';
    if(/access violation|segfault|segmentation fault|段错误|野指针|越界/i.test(joined)){concept='内存访问越界或指针指向了无效地址';clue='核对数组下标和最大下标是否停在长度减一；追踪指针是否在使用前被释放、置空或从未赋值。';}
    else if(/division|divide|除零|SIGFPE|0xC0000094|0xC000001D/i.test(joined)){concept='除法或取模运算遇到了 0';clue='在每次除法和 % 之前检查右操作数，尤其是平均值、循环长度或用户输入可能为 0 的情况。';}
    else if(/stack|递归太深|超大局部数组/i.test(joined)){concept='调用栈耗尽或局部数组过大';clue='检查递归是否有可达终止条件，并考虑把特别大的局部数组移到函数外或改为动态分配。';}
    else if(/assert|abort|SIGABRT/i.test(joined)){concept='程序触发了断言或标准库安全检查';clue='看运行信息里第一条断言/堆错误，在它之前记录输入和关键变量，定位第一次出现非法状态的位置。';}
    return {
      status:'RE',
      title:'运行现场',
      steps:[
        {label:'观察',title:c&&c.name?c.name+' 运行时终止':'程序在运行阶段终止',body:'运行信息显示：“'+first+'”。下方退出码和 stderr 是比猜测更可靠的第一手证据。'},
        {label:'判断',title:concept,body:'答案还没进入正常比对阶段；应先让程序稳定结束，再检查结果是否正确。'},
        {label:'线索',title:'从最危险的内存操作开始查',body:clue}
      ]
    };
  }

  function timeoutCoach(result){
    const kind=result.tle_kind||'';
    const c=failedCase(result);
    let concept='程序在时限内没有结束';
    let clue='找到最内层循环，检查循环变量是否每次都会向退出条件靠近；再用输入规模估算执行次数。';
    if(kind==='wait'){concept='CPU 几乎没消耗，程序更像是在等待而不是计算';clue='检查 scanf 是否在等待一个不会出现的值，循环是否会一直读取无效输入，以及是否有 sleep、阻塞 I/O 或死锁。';}
    else if(kind==='cpu'){concept='CPU 时间已经用满，主要问题是计算量或死循环';clue='检查循环终点是否会变化，并估算时间复杂度；注意双重循环、重复计算和每个用例都重新构造数据。';}
    else if(kind==='wall'){concept='程序一边计算一边等待，中途可能卡住';clue='先确认循环能够结束，再检查反复输入输出、阻塞调用和输入格式是否与题目一致。';}
    return {
      status:'TLE',
      title:'超时归因',
      steps:[
        {label:'观察',title:c&&c.name?c.name+' 没有在时限内结束':'判题在时限到达时强制停止了程序',body:result.time_ms!=null?'这次执行持续到约 '+result.time_ms+' ms。超时只说明“没结束”，不等于答案一定错。':'程序没有返回正常结束状态。'},
        {label:'判断',title:concept,body:'先区分“永远结束不了”和“数据规模太大”: 两类的修改方向完全不同。'},
        {label:'线索',title:'先证明循环会前进',body:clue}
      ]
    };
  }

  function systemCoach(result){
    return {
      status:'SE',
      title:'环境检查',
      steps:[
        {label:'观察',title:'这次是运行环境报告失败',body:clip(result.message||'判题服务暂时没有返回有效结果。',150)},
        {label:'判断',title:'当前结果不能证明代码正确或错误',body:'系统错误不会计入尝试次数和错题本，代码仍会保留。'},
        {label:'线索',title:'保留代码并重试',body:'先检查页面顶部的编译器状态；确认服务恢复后重新提交，不需要因此改写答案。'}
      ]
    };
  }

  function coach(result){
    if(!result||!result.status)return null;
    if(result.status==='CE')return compileCoach(result);
    if(result.status==='WA')return wrongAnswerCoach(result);
    if(result.status==='TLE')return timeoutCoach(result);
    if(result.status==='RE')return runtimeCoach(result);
    if(result.status==='SE')return systemCoach(result);
    return null;
  }

  function explain(result){
    const guide=coach(result);
    if(guide){
      const first=guide.steps[0],second=guide.steps[1],third=guide.steps[2];
      return {title:first.title,text:first.title+'。'+second.title+'。'+second.body+'。'+third.body};
    }
    if(result&&result.status==='AC')return {title:'这道题通过了，再独立走一步',text:'自己补一个最小值或边界输入，解释为什么这段代码仍然正确，再试着不看答案复写核心思路。'};
    return null;
  }

  return {coach,explain};
});
