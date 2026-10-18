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
  'togglePasswordVisibility,PASSWORD_MIN_LENGTH,isPremiumAccess,render,AccessApi,refreshManualPaymentState,submitManualPayment,copyManualDanaNumber,logoutAccess,claimPremiumPaymentToken,manualWhatsAppUrl,openManualWhatsApp');
harness=harness.replace('return { qa: window.__eqQA,','return { context, qa: window.__eqQA,');
const makeHarness=new Function('fs','path','vm','ROOT','ASSET_ORIGIN',harness+'; return makeHarness;')(fs,path,vm,ROOT,ASSET_ORIGIN);
const account={user_id:'buyer-id',email:'buyer@example.test',role:'user',anonymous:false,premium:false,demo_limit:5,demo_used:0,demo_remaining:5,used_question_ids:[]};
const config={ok:true,available:true,recipient_number:'SYNTHETIC-NOT-PAYABLE',recipient_name:'TEST ONLY',price_idr:5000,whatsapp_number:'628000000000',whatsapp_message:'Halo Admin English Quest, saya ingin membeli Premium seharga Rp5.000. Saya akan mengirimkan bukti pembayaran setelah transfer.'};
let groups=0;
function setup(overrides={}){
 let legacy=0,activated=0,created=0;
 const api={manualPaymentConfig:async()=>({...config}),manualPaymentOrders:async()=>({ok:true,orders:[]}),
 createManualPayment:async()=>{created++;throw Error('buyer order not allowed');},createPaymentOrder:async()=>{legacy++;throw Error('checkout not allowed');},
 claimPaymentToken:async()=>{legacy++;return {ok:false};},activate:async()=>{activated++;return {ok:false};},...overrides};
 const h=makeHarness({api});h.qa.setVerifiedAccount(account);h.qa.state.page='premium-access';h.qa.render();
 return {...h,counts:()=>({legacy,activated,created})};
}
(async()=>{
 {
  const h=setup({manualPaymentConfig:async()=>({ok:true,available:false,recipient_number:'',recipient_name:'',whatsapp_number:'',message:'Informasi pembayaran belum dikonfigurasi.'})});
  await h.qa.refreshManualPaymentState();
  assert.match(h.root.innerHTML,/Pembayaran belum tersedia/);assert.match(h.root.innerHTML,/disabled>HUBUNGI ADMIN VIA WHATSAPP/);
  assert.doesNotMatch(h.root.innerHTML,/SYNTHETIC|href="https:\/\/wa.me|payment-start|payment-claim|checkout_url/);groups++;
 }
 {
  const h=setup();let copied='';h.context.navigator.clipboard={writeText:async v=>{copied=v;}};
  await h.qa.refreshManualPaymentState();assert.match(h.root.innerHTML,/ENGLISH QUEST PREMIUM/);
  for(const value of ['Rp5.000','permanen','tanpa langganan','TEST ONLY','SYNTHETIC-NOT-PAYABLE','SALIN NOMOR DANA','HUBUNGI ADMIN VIA WHATSAPP'])assert.ok(h.root.innerHTML.includes(value),value);
  await h.qa.copyManualDanaNumber();assert.equal(copied,config.recipient_number);
  const link=new URL(h.qa.manualWhatsAppUrl());assert.equal(link.origin,'https://wa.me');assert.equal(link.pathname,'/628000000000');assert.equal(link.searchParams.get('text'),config.whatsapp_message);
  assert.match(h.root.innerHTML,/target="_blank" rel="noopener noreferrer"/);groups++;
 }
 {
  const h=setup();await h.qa.refreshManualPaymentState();let opened='';
  h.context.window.EnglishQuestAndroid={openExternal:url=>{opened=url;}};
  await h.qa.submitManualPayment();assert.equal(opened,h.qa.manualWhatsAppUrl());
  assert.equal(h.counts().created,0);assert.equal(h.counts().legacy,0);assert.equal(h.counts().activated,0);
  assert.equal(h.qa.isPremiumAccess(),false);assert.ok(!h.qa.state.accessTokenInput);
  assert.match(h.root.innerHTML,/Screenshot dan tombol WhatsApp bukan bukti/);
  assert.doesNotMatch(h.root.innerHTML,/Saya Sudah Membayar|payment-start|payment-claim|checkout_url/);groups++;
 }
 {
  const h=setup();await h.qa.refreshManualPaymentState();let opened;
  h.context.window.open=(...args)=>{opened=args;};h.qa.openManualWhatsApp();
  assert.equal(opened[0],h.qa.manualWhatsAppUrl());assert.equal(opened[2],'noopener,noreferrer');groups++;
 }
 {
  const h=setup({manualPaymentConfig:async()=>({ok:false,message:'Server tidak tersedia.'})});await h.qa.refreshManualPaymentState();
  assert.equal(h.qa.state.manualConfig,null);assert.equal(h.qa.isPremiumAccess(),false);assert.match(h.root.innerHTML,/Server tidak tersedia/);assert.equal(h.qa.state.paymentBusy,false);groups++;
 }
 {
  const h=setup({manualPaymentConfig:async()=>{throw Error('network');}});await h.qa.refreshManualPaymentState();
  assert.equal(h.qa.state.paymentBusy,false);assert.equal(h.qa.isPremiumAccess(),false);assert.match(h.root.innerHTML,/Tidak dapat terhubung/);groups++;
 }
 {
  const h=setup();await h.qa.refreshManualPaymentState();await h.qa.claimPremiumPaymentToken();
  assert.equal(h.counts().legacy,0);assert.ok(!h.qa.state.accessTokenInput);assert.equal(h.qa.isPremiumAccess(),false);
  await h.qa.logoutAccess();assert.equal(h.qa.state.manualConfig,null);
  h.qa.setVerifiedAccount({...account,user_id:'other',email:'other@example.test'});h.qa.state.page='premium-access';h.qa.render();
  assert.doesNotMatch(h.root.innerHTML,/SYNTHETIC-NOT-PAYABLE/);groups++;
 }
 {
  const h=setup({manualPaymentConfig:async()=>({...config,recipient_name:'<img src=x onerror=alert(1)>'})});await h.qa.refreshManualPaymentState();
  assert.doesNotMatch(h.root.innerHTML,/<img src=x/);assert.match(h.root.innerHTML,/&lt;img/);
  h.qa.state.manualConfig.whatsapp_number='628000000000?text=malicious';h.qa.render();
  assert.equal(h.qa.manualWhatsAppUrl(),'');assert.doesNotMatch(h.root.innerHTML,/href="https:\/\/wa.me/);groups++;
 }
 {
  const h=setup();await h.qa.logoutAccess();h.qa.state.page='premium-access';await h.qa.refreshManualPaymentState();
  assert.match(h.root.innerHTML,/TEST ONLY/);assert.match(h.root.innerHTML,/Hubungkan akun email/);assert.equal(h.qa.isPremiumAccess(),false);groups++;
 }
 {
  const java=fs.readFileSync(path.join(ROOT,'english-quest-android/app/src/main/java/com/englishquest/app/MainActivity.java'),'utf8');
  assert.ok(java.includes('"wa.me".equalsIgnoreCase(host)'));assert.ok(java.includes('uri.getPath().matches("/[1-9][0-9]{7,14}")'));assert.ok(java.includes('if (!allowedHost && !whatsappLink) return;'));
  groups++;
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
  assert.ok(!nodes.has('#create-form'));
  fail=false;nodes.get('#record-email').value='buyer@example.test';nodes.get('#record-amount').value='5000';nodes.get('#record-reference').value='SYNTHETIC';nodes.get('#record-confirm').checked=true;
  await nodes.get('#record-form').handlers.submit({preventDefault(){}});
  const record=requests.find(([url])=>url.endsWith('/record'));assert.ok(record);
  assert.equal(JSON.parse(record[1].body).buyer_email,'buyer@example.test');
  assert.equal(JSON.parse(record[1].body).confirmed_received,true);groups++;
 }
 console.log(`Manual DANA/WhatsApp UI: ${groups} groups passed (configuration, copy, URL/prefill, native/browser opening, no checkout/token, failure, isolation, public info, Android allowlist, admin).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
