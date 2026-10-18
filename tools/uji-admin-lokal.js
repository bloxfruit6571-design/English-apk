/* Invoked by isolated unittest; credentials arrive on stdin and are never logged. */
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
 const fixture=JSON.parse(fs.readFileSync(0,'utf8'));
 const html=await (await fetch(fixture.base+'/admin')).text();
 const client=await (await fetch(fixture.base+'/access-client.js')).text();
 const url=new URL(fixture.base+'/admin'),storage=new Map();
 const location={href:url.href,origin:url.origin,hostname:url.hostname,protocol:url.protocol};
 const ctx={window:{location},location,URL,fetch,AbortController,setTimeout,clearTimeout,
 localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}};
 vm.createContext(ctx);
 vm.runInContext(/<script data-local-admin>([\s\S]*?)<\/script>/.exec(html)[1],ctx);
 vm.runInContext(client,ctx);
 const api=ctx.window.EnglishQuestAccess;assert.equal(api.isConfigured(),true);
 const login=await api.login(fixture.email,fixture.password);assert.equal(login.ok,true);
 const me=await api.me();assert.equal(me.account.role,'admin');
 assert.equal(api.createPaymentOrder,undefined);assert.equal(api.claimPaymentToken,undefined);
 await api.logout();assert.equal(api.hasSession(),false);
 console.log('PASS real local admin client: bootstrap, login, role, logout; no checkout API exports.');
})().catch(()=>{console.error('Local admin client verification failed (credentials not logged).');process.exitCode=1;});
