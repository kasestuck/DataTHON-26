/* Real DOM tests without screenshots. Fixture routes are explicitly separate from real AI. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
const jpeg = require('./jpeg-fixtures.cjs');
const base = process.env.FULLSTACK_URL;
assert(base && new URL(base).hostname === '127.0.0.1', 'Only loopback testing is allowed');
const results = [];
const payload = (size = 24, name = 'sample.mov') => {
  const buffer = Buffer.alloc(size); buffer.writeUInt32BE(24); buffer.write('ftypqt  ', 4);
  return {name, mimeType:'video/quicktime', buffer};
};
const finding = (time, image = jpeg[0]) => ({type:'Синтетический тип', timecode_sec:time,
  description:'Синтетическое описание '.repeat(140), evidence_frames:[{timestamp_sec:time, image_base64:image}]});
const done = {status:'done', verdict:'violation', violations:[finding(62.4), finding(2.1, jpeg[1])]};
const clean = {status:'done', verdict:'no_violation', violations:[]};
const visible = async (page, selector) => page.locator(selector).waitFor({state:'visible',timeout:10000});
async function test(name, fn) { await fn(); results.push(name); console.log('PASS',name); }
async function noOverflow(page) {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Horizontal overflow');
}
async function scenario(browser, fn, options = {}) {
  const context = await browser.newContext({viewport:{width:390,height:844}});
  const page = await context.newPage(); const errors = [];
  page.on('pageerror', () => errors.push('Browser script error'));
  let uploads=0, polls=0; const headers=[];
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== base) return route.abort();
    if (url.pathname === '/config.js') return route.fulfill({contentType:'application/javascript',body:'window.CONFIG={POLL_MS:30,MAX_MB:100};'});
    if (url.pathname === '/ui-api/v1/session') return route.fulfill({json:{csrf_token:'synthetic-csrf'}});
    if (url.pathname === '/ui-api/v1/analyze') {
      uploads++; headers.push(request.headers());
      assert.match(request.headers()['content-type'],/^multipart\/form-data; boundary=/);
      assert(request.postDataBuffer().includes(Buffer.from('name="file"')));
      if (options.uploadError === 'network') return route.abort();
      return route.fulfill({status:options.uploadError || 200,json:options.uploadError ? {detail:'C:\\private\\secret'} : {job_id:'synthetic-job'}});
    }
    if (url.pathname.startsWith('/ui-api/v1/jobs/')) {
      polls++; headers.push(request.headers());
      if (options.pollNetwork) return route.abort();
      const value = options.pending || polls === 1 ? {status:'processing'} : (options.result || done);
      return route.fulfill({json:value});
    }
    return route.continue();
  });
  await page.goto(base+'/analyze.html');
  try {
    await fn({page,context,stats:()=>({uploads,polls,headers})});
    assert.deepEqual(errors,[]);
    for (const h of headers) { assert(!h['x-api-key']); assert.equal(h['x-csrf-token'],'synthetic-csrf'); }
  } finally { await context.close(); }
}
async function start(page, file=payload()) {
  await page.locator('#file').setInputFiles(file); await page.locator('#check').click();
}
async function fixtureTests(browser) {
  await test('upload → job_id → polling → sorted cards and real JPEG data URLs (synthetic API fixtures)', () => scenario(browser,async ({page,stats})=>{
    await page.locator('#file').setInputFiles(payload(29*1024*1024));
    assert.match(await page.locator('#filename').textContent(),/29\.0/);
    await page.locator('#check').evaluate(b=>{b.click();b.click();});
    await visible(page,'#screen-result'); assert.equal(stats().uploads,1);
    assert.equal(await page.locator('.card').count(),2);
    assert.match(await page.locator('.card .btn').first().textContent(),/00:02\.1/);
    assert.match(await page.locator('.card .btn').last().textContent(),/01:02\.4/);
    const images=page.locator('.frame img');
    assert.equal(await images.first().getAttribute('src'),'data:image/jpeg;base64,'+jpeg[1]);
    await page.waitForFunction(()=>[...document.querySelectorAll('.frame img')].every(i=>i.complete&&i.naturalWidth>0));
    assert.equal(await page.locator('.rule').count(),0);
    for (const width of [360,390,768,1440]) { await page.setViewportSize({width,height:900}); await noOverflow(page); }
    const before=stats().polls; await page.waitForTimeout(150); assert.equal(stats().polls,before);
    await page.locator('#again').click(); await start(page); await visible(page,'#screen-result'); assert.equal(stats().uploads,2);
  }));
  await test('no_violation: empty result with limitation notice',()=>scenario(browser,async ({page})=>{
    await start(page); await visible(page,'#screen-result'); assert.equal(await page.locator('.card').count(),0);
    assert.match(await page.locator('#result').textContent(),/не гарантирует/);
  },{result:clean}));
  await test('failed: safe error without verdict or private path',()=>scenario(browser,async ({page,stats})=>{
    await start(page); await visible(page,'#error');
    assert(!(await page.locator('#error').textContent()).includes('private'));
    assert(await page.locator('#screen-result').isHidden());
    const count=stats().polls;await page.waitForTimeout(150);assert.equal(stats().polls,count);
  },{result:{status:'failed',error:'C:\\private\\secret'}}));
  await test('malformed done never becomes no_violation',()=>scenario(browser,async ({page})=>{
    await start(page); await visible(page,'#error'); assert(await page.locator('#screen-result').isHidden());
  },{result:{status:'done'}}));
  await test('invalid and undecodable JPEG do not break results; no replacement frames',()=>scenario(browser,async ({page})=>{
    await start(page);await visible(page,'#screen-result');
    await page.waitForFunction(()=>document.querySelectorAll('.frames img').length===0);
    assert.equal(await page.locator('.frames img').count(),0);
    assert.equal(await page.locator('canvas').count(),0);
  },{result:{status:'done',verdict:'violation',violations:[finding(1,'not-base64'),finding(2,'/9j/2Q==')]}}));
  for (const code of [400,403,413,500,'network']) await test('upload error '+code,()=>scenario(browser,async ({page})=>{
    await start(page);await visible(page,'#error');assert(await page.locator('#check').isEnabled());
    assert(!(await page.locator('#error').textContent()).includes('private'));
  },{uploadError:code}));
  await test('poll network errors stop after retries',()=>scenario(browser,async ({page,stats})=>{
    await start(page);await visible(page,'#error');assert.equal(stats().polls,3);
  },{pollNetwork:true}));
  await test('cancel stops client polling without claiming server cancellation',()=>scenario(browser,async ({page,stats})=>{
    await start(page);await visible(page,'#screen-processing');await page.waitForTimeout(120);
    await page.locator('#cancel').click();await visible(page,'#error');
    assert.match(await page.locator('#error').textContent(),/Сервер может продолжить/);
    const count=stats().polls;await page.waitForTimeout(150);assert.equal(stats().polls,count);
  },{pending:true}));
  await test('page navigation stops polling',()=>scenario(browser,async ({page,stats})=>{
    await start(page);await page.waitForTimeout(120);await page.goto(base+'/about.html');
    const count=stats().polls;await page.waitForTimeout(150);assert.equal(stats().polls,count);
  },{pending:true}));
  await test('client rejects unsupported and empty files',()=>scenario(browser,async ({page,stats})=>{
    for (const file of [payload(24,'x.mp4'),{name:'empty.mov',mimeType:'video/quicktime',buffer:Buffer.alloc(0)}]) {
      await page.locator('#file').setInputFiles(file);await visible(page,'#error');assert(await page.locator('#check').isDisabled());
    }
    assert.equal(stats().uploads,0);
  }));
  await test('all pages at 360/390/768/1440 without horizontal scroll (DOM check only)',async()=>{
    const context=await browser.newContext();const page=await context.newPage();
    try { for (const width of [360,390,768,1440]) {
      await page.setViewportSize({width,height:900});
      for (const path of ['/','/analyze.html','/about.html','/profile.html']) {await page.goto(base+path);await noOverflow(page);}
    }} finally { await context.close(); }
  });
}
async function realVideo(browser, path, name) {
  const context=await browser.newContext({viewport:{width:390,height:844}}); const page=await context.newPage();
  const before=crypto.createHash('sha256').update(fs.readFileSync(path)).digest('hex');
  let uploaded=0,polled=0,last=null,unsafe=0;const errors=[];let external=0;const responseTasks=[];
  page.on('pageerror',()=>errors.push('Browser error'));
  await context.route('**/*',route=>{
    if (new URL(route.request().url()).origin!==base) {external++;return route.abort();}
    if (route.request().headers()['x-api-key']) unsafe++;
    return route.continue();
  });
  page.on('response',response=>{
    if(response.url().endsWith('/ui-api/v1/analyze')&&response.ok())uploaded++;
    if(response.url().includes('/ui-api/v1/jobs/')&&response.ok()) {
      polled++;responseTasks.push(response.json().then(value=>{last=value;}));
    }
  });
  const started=Date.now();
  try {
    await page.goto(base+'/analyze.html');await start(page,path);
    await page.waitForFunction(()=>!document.querySelector('#screen-result').classList.contains('is-hidden')||!document.querySelector('#error').classList.contains('is-hidden'),{},{timeout:310000});
    // DOM updates and this independent response observer complete asynchronously.
    // Await the response body before checking the terminal job status.
    await Promise.all(responseTasks);
    assert.equal(uploaded,1);assert(polled>0);assert.equal(unsafe,0);assert.equal(external,0);assert.deepEqual(errors,[]);
    assert(last,'Missing job response');
    if(name==='corrupt') {
      assert.equal(last.status,'failed');assert(!last.verdict);assert(await page.locator('#screen-result').isHidden());
    } else {
      assert.equal(last.status,'done', 'API status: '+last.status+'; UI error: '+await page.locator('#error').textContent());
      assert(await page.locator('#screen-result').isVisible());
      if(name==='positive')assert.equal(last.verdict,'violation');
      assert.equal(await page.locator('.card').count(),last.violations.length);
      await page.waitForFunction(()=>[...document.querySelectorAll('.frame img')].every(i=>i.complete&&i.naturalWidth>0));
      assert.equal(await page.locator('.frame img').count(),last.violations.reduce((n,v)=>n+v.evidence_frames.length,0));
      await noOverflow(page);
    }
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path)).digest('hex'),before);
    return {case:name,is_mock:false,status:last.status,verdict:last.verdict||null,findings:last.violations?.length||0,
      displayed_jpegs:await page.locator('.frame img').count(),wall_sec:(Date.now()-started)/1000,polls:polled,source_sha256:before};
  } finally {await context.close();}
}
(async()=>{
  const browser=await chromium.launch({channel:process.env.BROWSER_CHANNEL||'chrome',headless:true,timeout:30000});
  try {
    await fixtureTests(browser);
    const real=[];
    for(const [key,name] of [['POSITIVE_VIDEO','positive'],['NEGATIVE_VIDEO','negative'],['CORRUPT_VIDEO','corrupt']]) {
      if(process.env[key]) {const result=await realVideo(browser,process.env[key],name);real.push(result);console.log('REAL',JSON.stringify(result));}
    }
    const summary={fixture_tests_passed:results.length,real_browser_cases:real,screenshots_created:0};
    if(process.env.FULLSTACK_REPORT)fs.writeFileSync(process.env.FULLSTACK_REPORT,JSON.stringify(summary,null,2));
    console.log('SUMMARY',JSON.stringify(summary));
  } finally {await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
