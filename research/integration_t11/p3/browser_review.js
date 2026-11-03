async page => {
  const selector = page.getByLabel('选择回放情景');
  const slider = page.getByRole('slider', {name:'选择回放关键帧'});
  const verify = (ok, message) => { if (!ok) throw new Error(message); };
  await selector.selectOption('normal_cycle');
  await slider.focus();
  await slider.press('Home');
  verify(await page.locator('#prev').isDisabled(), 'start boundary');
  await page.getByRole('button', {name:'下一帧'}).click();
  verify(await slider.inputValue() === '1', 'next button');
  await page.getByRole('button', {name:'上一帧'}).click();
  verify(await slider.inputValue() === '0', 'previous button');
  await slider.press('End');
  verify(await page.locator('#next').isDisabled(), 'end boundary');
  await slider.press('ArrowLeft');
  verify(!await page.locator('#next').isDisabled(), 'keyboard seek');
  const result = await page.evaluate(() => {
    const data = JSON.parse(document.querySelector('#replay-data').textContent);
    const $ = id => document.getElementById(id);
    const get = id => $(id).textContent;
    const require = (ok, key) => { if (!ok) throw new Error(key); };
    const change = (id, value, event) => {
      $(id).value = value;
      $(id).dispatchEvent(new Event(event, {bubbles:true}));
    };
    const visit = (c, frame) => change('frame', c.frames.indexOf(frame), 'input');
    const at = (name, t) => {
      const c = data.cases.find(c => c.id === name);
      change('case', name, 'change');
      const f = c.frames.find(f => f.t === t);
      require(f, 'missing frame '+name+' '+t);
      visit(c, f);
    };
    let count = 0;
    for (const c of data.cases) {
      change('case', c.id, 'change');
      require(/[\u3400-\u9fff]/.test($('case').selectedOptions[0].text), 'untranslated case '+c.id);
      for (const f of c.frames) {
        visit(c, f);
        require(!/undefined|NaN|\[object Object\]/.test(document.body.innerText), 'invalid text '+c.id);
        require(get('queue') === f.queue+' 条' && get('drops') === f.drops+' 条', 'queue counters');
        require(get('notices') === f.newly_notified+' 条', 'new notice count');
        const seconds = f.local.remaining_s;
        const displayed = /\d/.test(get('estimate'));
        require(displayed === (f.local.prediction==='conditional' && Number.isFinite(seconds)), 'stale local estimate '+c.id);
        const remoteNumber = get('remote-explain').includes('当时的条件上界');
        require(remoteNumber === (f.remote?.prediction==='conditional' && Number.isFinite(f.remote.remaining_s)), 'stale remote estimate '+c.id);
        count++;
      }
    }
    at('wet_reconnect',62);
    require(get('wet')==='已受水' && get('forecast')==='无有效预报' && get('r-wet')==='当前干燥', 'local sensing versus remote');
    require(get('delivery').includes('待同步') && get('received')==='00:26', 'queued while offline');
    at('wet_reconnect',148);
    require(get('received')==='02:28' && get('saved')==='02:28' && get('remote-title').includes('受水'), 'reconnect');
    for (const name of ['change_offline','boot_offline']) {
      at(name,74);
      require(get('remote-explain').includes('19 分 50 秒') && !get('estimate').includes('分'), 'offline old evidence');
      at(name,101);
      require(!get('remote-explain').includes('19 分') && get('received')==='00:26', 'offline expiry');
      at(name,148);
      require(get('remote-title')==='等待有效观测', 'identity restored old evidence');
    }
    at('forecast_wet',62);
    require(get('forecast')==='当前未报风险' && get('wet')==='已受水', 'independent risk and wet');
    for (const [t,expected] of [[36,'稍后有降雨风险'],[60,'当前有降雨风险'],[90,'无有效预报']]) {
      at('forecast_boundary',t);
      require(get('r-forecast')===expected && get('received')==='00:36', 'remote forecast transition');
    }
    at('missing_packet',26); require(get('estimate')==='上界未知', 'unresolved');
    at('model_mismatch',26); require(get('estimate').includes('不匹配'), 'mismatch');
    at('contact_missing',35); require(get('contact')==='未接触', 'contact');
    for (const t of [35,36]) {
      at('fault_revoke',t); require(!/\d/.test(get('estimate')) && get('headline').includes('故障'), 'fault next frame');
      at('probe_obstructed',t); require(get('wet')==='无有效读数' && !/\d/.test(get('estimate')), 'obstructed probe');
    }
    at('ack_lost',68); require(get('notices')==='1 条', 'first wet notice');
    at('ack_lost',148); require(get('notices')==='0 条', 'duplicate notice');
    at('queue_full',35); require(parseInt(get('drops'),10)>0, 'history overflow');
    at('no_charge',60); require(get('received')==='—' && get('events').includes('电量不足'), 'denied send');
    for (const [t,expected] of [[4,'受充电干扰'],[14,'未知'],[50,'已排除干扰']]) {
      at('manual_charge',t); require(get('heat')===expected && !/\d/.test(get('estimate')), 'charge recovery');
    }
    at('normal_cycle',26);
    require(get('analysis')==='00:20' && get('expiry')==='01:40' && get('conditions').includes('相对湿度'), 'prediction provenance');
    require(document.querySelector('.remote-info').innerText.includes('真实手机送达') &&
            document.querySelector('.remote-info').innerText.includes('未知'), 'unknown phone status');
    return {cases:data.cases.length, frames:count};
  });
  await selector.selectOption('wet_reconnect');
  const widths = [320,390,768,1344];
  for (const width of widths) {
    await page.setViewportSize({width, height:900});
    verify(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'horizontal overflow '+width);
  }
  verify(await page.locator('script[src],link[href^="http"],img[src^="http"]').count()===0, 'external dependency');
  return {...result, widths, keyboard:true, semanticChecks:true};
}
