/* Actual app.js + admin script in isolated DOM stubs; never a real payment. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const ROOT=path.resolve(__dirname,'..');
const ASSET_ORIGIN='https://appassets.androidplatform.net';
// Reuse the existing tested DOM harness without changing any older assertions.
const registration=fs.readFileSync(path.join(__dirname,'client_registration.test.js'),'utf8');
let harness=registration.slice(registration.indexOf('function makeHarness('),registration.indexOf('function withDeadline('));
harness=harness.replace('togglePasswordVisibility,PASSWORD_MIN_LENGTH,isPremiumAccess,render,AccessApi',
  'togglePasswordVisibility,PASSWORD_MIN_LENGTH,isPremiumAccess,render,AccessApi,refreshManualPaymentState,submitManualPayment,copyManualDanaNumber,logoutAccess,claimPremiumPaymentToken');
harness=harness.replace('return { qa: window.__eqQA,','return { context, qa: window.__eqQA,');
const makeHarness=new Function('fs','path','vm','ROOT','ASSET_ORIGIN',harness+'; return makeHarness;')(fs,path,vm,ROOT,ASSET_ORIGIN);
const account={user_id:'buyer-id',email:'buyer@example.test',role:'user',anonymous:false,premium:false,demo_limit:5,demo_used:0,demo_remaining:5,used_question_ids:[]};
const config={ok:true,available:true,recipient_number:'SYNTHETIC-NOT-PAYABLE',recipient_name:'TEST ONLY',price_idr:5000};
let groups=0;
function setup(overrides={}){
 let legacy=0,activated=0;
 const api={manualPaymentConfig:async()=>({...config}),manualPaymentOrders:async()=>({ok:true,orders:[]}),
 createManualPayment:async()=>({ok:true,order_id:'EQM-TEST',user_id:'buyer-id',status:'pending',token_issued:false}),
 claimPaymentToken:async()=>{legacy++;return {ok:false};},activate:async()=>{activated++;return {ok:false};},...overrides};
 const h=makeHarness({api});h.qa.setVerifiedAccount(account);h.qa.state.page='premium-access';h.qa.render();
 return {...h,counts:()=>({legacy,activated})};
}
(async()=>{
 {
  const h=setup({manualPaymentConfig:async()=>({ok:true,available:false,recipient_number:'',recipient_name:'',message:'Pembayaran belum tersedia.'})});
  await h.qa.refreshManualPaymentState();
  assert.match(h.root.innerHTML,/Pembayaran belum tersedia/);assert.match(h.root.innerHTML,/data-action="payment-start" disabled/);
  assert.doesNotMatch(h.root.innerHTML,/SYNTHETIC|payment-claim|checkout_url/);groups++;
 }
 {
  const h=setup();let copied='';h.context.navigator.clipboard={writeText:async v=>{copied=v;}};
  await h.qa.refreshManualPaymentState();
  assert.match(h.root.innerHTML,/Rp5.000/);assert.match(h.root.innerHTML,/tanpa langganan/);
  assert.match(h.root.innerHTML,/Saya Sudah Membayar/);assert.match(h.root.innerHTML,/Salin Nomor DANA/);
  await h.qa.copyManualDanaNumber();assert.equal(copied,config.recipient_number);
  await h.qa.submitManualPayment();
  assert.equal(h.qa.state.manualOrders[0].status,'pending');assert.equal(h.qa.isPremiumAccess(),false);
  assert.equal(h.counts().activated,0);assert.equal(h.counts().legacy,0);assert.ok(!h.qa.state.accessTokenInput);
  assert.match(h.root.innerHTML,/EQM-TEST/);groups++;
 }
 {
  const h=setup({createManualPayment:async()=>({ok:false,message:'Server tidak tersedia.'})});await h.qa.refreshManualPaymentState();await h.qa.submitManualPayment();
  assert.equal(h.qa.state.manualOrders.length,0);assert.equal(h.qa.isPremiumAccess(),false);assert.match(h.root.innerHTML,/Server tidak tersedia/);assert.equal(h.qa.state.paymentBusy,false);groups++;
 }
 {
  const h=setup({createManualPayment:async()=>{throw Error('network');}});await h.qa.refreshManualPaymentState();await h.qa.submitManualPayment();
  assert.equal(h.qa.state.paymentBusy,false);assert.equal(h.qa.isPremiumAccess(),false);assert.match(h.root.innerHTML,/Koneksi gagal/);groups++;
 }
 {
  const h=setup({manualPaymentOrders:async()=>({ok:true,orders:[{order_id:'EQM-OLD',status:'issued',token_issued:true,token_status:'available'}]})});
  await h.qa.refreshManualPaymentState();await h.qa.claimPremiumPaymentToken();
  assert.match(h.root.innerHTML,/EQM-OLD/);assert.match(h.root.innerHTML,/admin/);
  assert.doesNotMatch(h.root.innerHTML,/data-action="payment-claim"/);assert.equal(h.counts().legacy,0);assert.ok(!h.qa.state.accessTokenInput);assert.equal(h.qa.isPremiumAccess(),false);groups++;
 }
 {
  const h=setup();await h.qa.refreshManualPaymentState();await h.qa.submitManualPayment();await h.qa.logoutAccess();
  assert.equal(h.qa.state.manualOrders.length,0);assert.equal(h.qa.state.manualConfig,null);
  h.qa.setVerifiedAccount({...account,user_id:'other',email:'other@example.test'});h.qa.state.page='premium-access';h.qa.render();
  assert.doesNotMatch(h.root.innerHTML,/EQM-TEST|SYNTHETIC-NOT-PAYABLE/);groups++;
 }
 {
  const h=setup({manualPaymentConfig:async()=>({...config,recipient_name:'<img src=x onerror=alert(1)>'})});await h.qa.refreshManualPaymentState();
  assert.doesNotMatch(h.root.innerHTML,/<img src=x/);assert.match(h.root.innerHTML,/&lt;img/);groups++;
 }
 {
  let done,calls=0;const h=setup({createManualPayment:()=>{calls++;return new Promise(resolve=>{done=resolve;});}});
  await h.qa.refreshManualPaymentState();const a=h.qa.submitManualPayment();await h.qa.submitManualPayment();
  assert.equal(calls,1);done({ok:true,order_id:'EQM-ONCE',status:'pending'});await a;assert.equal(h.qa.state.paymentBusy,false);groups++;
 }
 {
  const requests=[];const store=new Map([['englishQuest.session.v1','synthetic-session']]);
  const context={window:{EQ_API_BASE:'https://fixture.invalid'},location:{origin:ASSET_ORIGIN},URL,AbortController,setTimeout,clearTimeout,
   localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
   fetch:async(url,options)=>{requests.push([url,options]);return {ok:true,status:200,json:async()=>({ok:true,orders:[]})};}};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(ROOT,'access-client.js'),'utf8'),context);
  const api=context.window.EnglishQuestAccess;
  await api.manualPaymentConfig();await api.createManualPayment();await api.manualPaymentOrders();
  assert.ok(requests.every(([url])=>url.includes('/api/manual-payments/')));
  assert.equal(requests[1][1].body,'{}');assert.ok(!requests.some(([url])=>url.includes('/payments/claim')));groups++;
 }
 {
  // Real admin inline JS: authorized/failed responses, one-time code and audit events.
  const html=fs.readFileSync(path.join(ROOT,'backend/admin.html'),'utf8');
  const nodes=new Map();
  for(const m of html.matchAll(/id="([^"]+)"/g))nodes.set('#'+m[1],{value:'',style:{},hidden:false,checked:false,textContent:'',innerHTML:'',handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}});
  nodes.get('#order-status').value='pending';nodes.get('#status').value='all';
  let fail=false;const requests=[];
  const ctx={AbortController,document:{querySelector:s=>{assert.ok(nodes.has(s),'missing element '+s);return nodes.get(s);}},
   window:{EnglishQuestAccess:{hasSession:()=>false}},location:{origin:'https://fixture.invalid'},
   localStorage:{getItem:()=> 'synthetic-session'},navigator:{clipboard:{writeText:async()=>{}}},URLSearchParams,Date,Number,String,JSON,Promise,console,setTimeout,clearTimeout,confirm:()=>true,prompt:()=>null,
   fetch:async(url,opt)=>{requests.push([url,opt]);let result={ok:true,orders:[],tokens:[],summary:{}};
     if(url.endsWith('/issue'))result=fail?{ok:false,message:'Admin tidak berwenang.'}:{ok:true,order_id:'EQM-TEST',user_id:'buyer-id',token:'SYNTHETIC-ONE-TIME-NOT-VALID'};
     return {status:fail?403:200,json:async()=>result};}};
  vm.createContext(ctx);vm.runInContext(/<script>([\s\S]*?)<\/script>/.exec(html)[1]+'\nwindow.qa={manualAction,loadOrders};',ctx);
  await ctx.window.qa.manualAction('issue','EQM-TEST');assert.match(nodes.get('#code-list').innerHTML,/SYNTHETIC-ONE-TIME/);
  assert.equal(nodes.get('#new-codes').style.display,'block');
  nodes.get('#code-list').innerHTML='';fail=true;await ctx.window.qa.manualAction('issue','EQM-TEST');
  assert.equal(nodes.get('#code-list').innerHTML,'');assert.match(nodes.get('#global-error').textContent,/tidak berwenang/);
  assert.ok(requests.some(([url])=>url.endsWith('/api/admin/manual-payments/issue')));
  assert.ok(!nodes.has('#create-form'));groups++;
 }
 console.log(`Manual payments UI: ${groups} groups passed (buyer, copy, pending, offline, reload, account isolation, XSS escaping, duplicate tap, real client routes, admin).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
