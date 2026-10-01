import assert from 'node:assert/strict';
const {chromium}=await import(new URL('../.test-cache/climate-browser/node_modules/playwright-core/index.mjs',import.meta.url));
const base=process.argv[2]||'http://127.0.0.1:4173/';
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/home/users/kieran/.cache/ms-playwright/chromium_headless_shell-1187/chrome-linux/headless_shell',ignoreDefaultArgs:['--disable-dev-shm-usage'],args:['--no-sandbox']});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:2}), errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'?tab=climatology');
 await page.waitForFunction(()=>document.querySelectorAll('#mlaClassChart rect').length===54);
 await page.evaluate(()=>document.fonts.ready);
 const verify=async()=>{
  const result=await page.$eval('#mlaClassChart',svg=>{
   const table=[...document.querySelectorAll('#mlaClassData tbody tr')].map(row=>[...row.querySelectorAll('td')].slice(1).map(cell=>Number(cell.textContent.replaceAll(',',''))));
   const cells=[...svg.querySelectorAll('[data-value]')].map(cell=>({row:Number(cell.dataset.row),column:Number(cell.dataset.column),value:Number(cell.dataset.value)}));
   const bounds=svg.getBoundingClientRect();
   const clipped=[...svg.querySelectorAll('text')].filter(label=>{
    const b=label.getBoundingClientRect();
    return b.left<bounds.left-1||b.right>bounds.right+1||b.top<bounds.top-1||b.bottom>bounds.bottom+1;
   }).map(label=>label.textContent);
   return {tag:svg.tagName,namespace:svg.namespaceURI,table,cells,clipped,raster:svg.querySelectorAll('image,canvas,foreignObject').length,viewBox:svg.viewBox.baseVal.width,width:svg.clientWidth};
  });
  assert.equal(result.tag,'svg');assert.equal(result.namespace,'http://www.w3.org/2000/svg');
  assert.equal(result.raster,0);assert.deepEqual(result.clipped,[]);
  assert.equal(result.viewBox,result.width);
  assert.equal(result.cells.length,result.table.length*6);
  for(const cell of result.cells)assert.ok(Math.abs(cell.value-result.table[cell.row][cell.column])<=.0051,'SVG value agrees with accessible data');
  return result;
 };
 const initial=await verify();assert.ok(initial.cells.some(cell=>cell.value>0));
 await page.locator('#mlaClassChart').screenshot({path:'.test-cache/era5-browser/class-decades-svg-desktop.png'});
 console.log('PASS vector-only chart, 54 cells and unchanged data');
 // Exercise the existing date filter rather than replacing chart data in tests.
 await page.$eval('#mlaYearMin',input=>{input.value='2010';input.dispatchEvent(new Event('change',{bubbles:true}));});
 await page.waitForFunction(()=>document.querySelectorAll('#mlaClassChart rect').length===12);
 await verify();console.log('PASS filtered decades');
 await page.fill('#mlaSearch','no such cyclone vector-chart-check');
 await page.waitForFunction(()=>[...document.querySelectorAll('#mlaClassChart [data-value]')].every(cell=>Number(cell.dataset.value)===0));
 await verify();console.log('PASS empty subset');
 await page.goto(base+'?tab=climatology');
 await page.waitForFunction(()=>document.querySelectorAll('#mlaClassChart rect').length===54);
 await page.setViewportSize({width:390,height:844});
 await page.waitForFunction(()=>{const svg=document.querySelector('#mlaClassChart');return svg.viewBox.baseVal.width===svg.clientWidth;});
 await verify();await page.locator('#mlaClassChart').screenshot({path:'.test-cache/era5-browser/class-decades-svg-mobile.png'});
 console.log('PASS responsive mobile labels and cells');
 await page.setViewportSize({width:1440,height:1100});
 await page.evaluate(()=>{document.documentElement.style.zoom='2';window.dispatchEvent(new Event('resize'));});
 await page.waitForFunction(()=>{const svg=document.querySelector('#mlaClassChart');return svg.viewBox.baseVal.width===svg.clientWidth;});
 await verify();
 await page.waitForFunction(()=>document.querySelector('#mlaToast').dataset.visible!=='true');
 await page.waitForTimeout(200);
 await page.locator('#mlaClassChart').screenshot({path:'.test-cache/era5-browser/class-decades-svg-zoom.png'});
 assert.deepEqual(errors,[]);console.log('PASS 200% zoom and zero page errors');
} finally {await browser.close();}
