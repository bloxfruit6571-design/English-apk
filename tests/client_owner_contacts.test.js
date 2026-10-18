'use strict';
// Real client + app scripts; public contacts only, never contact WhatsApp/DANA.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..'),ASSET_ORIGIN='https://appassets.androidplatform.net';
const publicData=JSON.parse(fs.readFileSync(path.join(ROOT,'premium-contact.json'),'utf8'));
const MESSAGE='Halo Admin English Quest.\n\nSaya ingin membeli Premium seharga Rp5.000.\n\nSaya sudah melakukan pembayaran melalui DANA.\nBerikut saya kirimkan screenshot bukti transfer untuk diperiksa.\n\nMohon verifikasi pembayaran saya dan berikan token Premium\njika pembayaran sudah benar-benar diterima.\n\nTerima kasih.';
const registration=fs.readFileSync(path.join(__dirname,'client_registration.test.js'),'utf8');
let harness=registration.slice(registration.indexOf('function makeHarness('),registration.indexOf('function withDeadline('));
harness=harness.replace('togglePasswordVisibility,PASSWORD_MIN_LENGTH,isPremiumAccess,render,AccessApi','togglePasswordVisibility,PASSWORD_MIN_LENGTH,isPremiumAccess,render,AccessApi,refreshManualPaymentState,copyManualDanaNumber,manualWhatsAppUrl,openManualWhatsApp,logoutAccess');
harness=harness.replace('return { qa: window.__eqQA,','return { context, qa: window.__eqQA,');
const makeHarness=new Function('fs','path','vm','ROOT','ASSET_ORIGIN',harness+';return makeHarness;')(fs,path,vm,ROOT,ASSET_ORIGIN);
function client({native=true,remote='offline',corrupt=false}={}){
 const requests=[],storage=new Map();
 const location={href:ASSET_ORIGIN+'/assets/index.html',origin:ASSET_ORIGIN,hostname:'appassets.androidplatform.net',protocol:'https:'};
 const window={location,EQ_API_BASE:'https://account.invalid'};
 if(native)window.EnglishQuestAndroid={getPremiumContactConfig:()=>corrupt?'bad json':JSON.stringify(publicData)};
 const ctx={window,location,URL,AbortController,setTimeout,clearTimeout,
 localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
 fetch:async(url,options)=>{
  requests.push(String(url));assert.ok(!String(url).startsWith('https://wa.me'),'no actual WhatsApp requests');
  if(String(url).endsWith('/premium-contact.json'))return {ok:true,status:200,json:async()=>corrupt?{}:publicData};
  if(String(url).endsWith('/manual-payments/config')&&remote==='disabled')return {ok:true,status:200,json:async()=>({ok:true,available:false,recipient_number:'',recipient_name:'',whatsapp_number:'',message:'Pembayaran dinonaktifkan pemilik.'})};
  throw Error('synthetic offline');
 }};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(ROOT,'access-client.js'),'utf8'),ctx);
 return {api:ctx.window.EnglishQuestAccess,requests,ctx};
}
const account={user_id:'fixture-buyer',email:'buyer@example.test',role:'user',anonymous:false,premium:false,demo_limit:5,demo_used:0,demo_remaining:5,used_question_ids:[]};
async function setup(options){
 const c=client(options),h=makeHarness({api:c.api});await new Promise(setImmediate);
 h.qa.setVerifiedAccount(account);h.qa.state.page='premium-access';await h.qa.refreshManualPaymentState();
 return {...h,c};
}
let passed=0;
(async()=>{
 {
  const h=await setup();assert.match(h.root.innerHTML,/0856-5670-6104/);
  assert.ok((h.root.innerHTML.match(/0856-5670-6104/g)||[]).length>=2);
  assert.match(h.root.innerHTML,/Harga: Rp5.000,00/);assert.match(h.root.innerHTML,/belum dikonfirmasi/);
  assert.equal(h.qa.state.manualConfig.recipient_name,'');assert.equal(h.qa.state.manualConfig.source,'app');
  assert.match(h.root.innerHTML,/Server belum dapat dijangkau/);assert.equal(h.qa.isPremiumAccess(),false);passed++;
 }
 {
  const h=await setup();let value='';h.context.window.EnglishQuestAndroid={copyDanaNumber:v=>{value=v;return true;}};
  h.context.navigator.clipboard={writeText:()=>{throw Error('native should be first');}};
  await h.qa.copyManualDanaNumber();assert.equal(value,'085656706104');assert.equal(h.qa.state.paymentFeedback,'Nomor DANA berhasil disalin');assert.equal(h.qa.isPremiumAccess(),false);passed++;
 }
 {
  const h=await setup({native:false});let value='';h.context.navigator.clipboard={writeText:async v=>{value=v;}};
  await h.qa.copyManualDanaNumber();assert.equal(value,'085656706104');assert.equal(h.qa.state.paymentFeedback,'Nomor DANA berhasil disalin');
  assert.ok(h.c.requests.some(u=>u.endsWith('/premium-contact.json')));passed++;
 }
 {
  const h=await setup();let selected='',removed=false;
  h.context.window.EnglishQuestAndroid={copyDanaNumber:()=>false};h.context.navigator.clipboard={writeText:async()=>{throw Error('blocked');}};
  h.context.document.createElement=()=>({value:'',style:{},setAttribute(){},select(){selected=this.value;},remove(){removed=true;}});
  h.context.document.body={appendChild(){}};h.context.document.execCommand=command=>command==='copy';
  await h.qa.copyManualDanaNumber();assert.equal(selected,'085656706104');assert.ok(removed);assert.equal(h.qa.state.paymentFeedback,'Nomor DANA berhasil disalin');passed++;
 }
 {
  const h=await setup();h.context.window.EnglishQuestAndroid={copyDanaNumber:()=>false};
  h.context.navigator.clipboard={writeText:async()=>{throw Error('blocked');}};
  await h.qa.copyManualDanaNumber();assert.match(h.qa.state.paymentFeedback,/belum dapat disalin/);
  assert.doesNotMatch(h.qa.state.paymentFeedback,/berhasil disalin/);assert.equal(h.qa.isPremiumAccess(),false);passed++;
 }
 {
  const h=await setup();const link=new URL(h.qa.manualWhatsAppUrl());
  assert.equal(link.origin,'https://wa.me');assert.equal(link.pathname,'/6285656706104');assert.equal(link.searchParams.get('text'),MESSAGE);
  assert.match(h.root.innerHTML,/KONFIRMASI PEMBAYARAN VIA WHATSAPP/);assert.match(h.root.innerHTML,/Draft WhatsApp dapat diedit/);
  let opened='';h.context.window.EnglishQuestAndroid={openExternal:url=>{opened=url;}};h.qa.openManualWhatsApp();
  assert.equal(opened,link.href);assert.equal(h.qa.isPremiumAccess(),false);assert.ok(!h.qa.state.accessTokenInput);passed++;
 }
 {
  const h=await setup();assert.equal(h.c.api.createPaymentOrder,undefined);assert.equal(h.c.api.claimPaymentToken,undefined);
  assert.equal(h.c.api.createManualPayment,undefined);assert.equal(h.c.api.manualPaymentOrders,undefined);
  assert.ok(h.c.requests.every(u=>!u.includes('/payments/order')&&!u.includes('/payments/claim')&&!u.includes('/premium/activate')));
  assert.doesNotMatch(h.root.innerHTML,/checkout_url|data-action="payment-start"|data-action="payment-claim"/);passed++;
 }
 {
  const h=await setup({remote:'disabled'});assert.equal(h.qa.state.manualConfig.available,false);assert.equal(h.qa.manualWhatsAppUrl(),'');
  assert.doesNotMatch(h.root.innerHTML,/0856-5670-6104/);assert.match(h.root.innerHTML,/Pembayaran dinonaktifkan pemilik/);passed++;
 }
 {
  const h=await setup({corrupt:true});assert.equal(h.qa.state.manualConfig,null);assert.equal(h.qa.manualWhatsAppUrl(),'');
  assert.doesNotMatch(h.root.innerHTML,/0856-5670-6104/);assert.equal(h.qa.isPremiumAccess(),false);passed++;
 }
 {
  const h=await setup();h.qa.state.page='demo-intro';h.qa.render();
  assert.match(h.root.innerHTML,/satu misi Easy dengan maksimal lima pertanyaan unik/);assert.doesNotMatch(h.root.innerHTML,/dua misi Easy/);passed++;
 }
 console.log(`Owner public contact: ${passed} groups passed (exact numbers/price, unknown name, native/browser/fallback clipboard, failure honesty, editable WhatsApp draft, no automatic entitlement, server disable, corrupt config, demo copy).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
