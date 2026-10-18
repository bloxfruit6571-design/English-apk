const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const baseAccount = (extra = {}) => ({
  user_id: 'demo-user', email: 'learner@example.test', role: 'user', anonymous: false,
  premium: false, demo_limit: 5, demo_used: 0, demo_remaining: 5,
  used_question_ids: [], ...extra
});
function makeApi(extra = {}) {
  return {
    isConfigured: () => true, hasSession: () => false,
    health: async () => ({ ok: true, status: 200 }),
    startDemo: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' }),
    me: async () => ({ ok: false, code: 'AUTH_REQUIRED', message: 'not logged in' }),
    register: async () => ({ ok: false }), login: async () => ({ ok: false }),
    activate: async () => ({ ok: false }),
    consume: async () => ({ ok: false, code: 'NETWORK_ERROR', message: 'offline' }),
    syncDemo: async () => ({ ok: true, imported: 0 }),
    logout: async () => ({ ok: true }), clearSession() {},
    ...extra
  };
}
function makeHarness({ profile, api } = {}) {
  const html = { value: '' };
  const appRoot = { addEventListener() {}, get innerHTML() { return html.value; }, set innerHTML(v) { html.value = String(v); } };
  const elements = new Map();
  const doc = {
    querySelector(selector) { return selector === '#app' ? appRoot : (elements.get(selector)||null); },
    querySelectorAll() { return []; }, addEventListener() {}, visibilityState: 'visible',
    documentElement: { classList: { toggle() {} }, style: { setProperty() {} } }
  };
  const stored = new Map();
  if (profile) stored.set('englishQuest.profile.v1', JSON.stringify(profile));
  const localStorage = { getItem: k => stored.get(k) || null, setItem: (k,v) => stored.set(k,String(v)), removeItem: k => stored.delete(k) };
  const accessApi = api || makeApi();
  const window = {
    location: { href: 'https://appassets.androidplatform.net/assets/index.html', origin: 'https://appassets.androidplatform.net', hostname: 'appassets.androidplatform.net', protocol: 'https:' },
    scrollTo() {}, addEventListener() {}, EnglishQuestAccess: accessApi
  };
  const context = {
    window, document: doc, localStorage, navigator: {}, console,
    setTimeout: () => 1, clearTimeout() {}, Date, Math, JSON, Map, Set, Object, Array, String, Number, Boolean, RegExp, Intl, URL,
    Blob: class Blob {}, confirm: () => false, performance: { now: () => 0 }, fetch: async () => { throw new Error('offline'); }
  };
  vm.createContext(context);
  for (const filename of ['library-data.js','mission-data.js','mission-data-extras.js']) {
    vm.runInContext(fs.readFileSync(path.join(ROOT,filename),'utf8'), context, { filename });
  }
  const source = fs.readFileSync(path.join(ROOT,'app.js'),'utf8') + `\nwindow.__eqQA={state,profile,get accessState(){return accessState},setVerifiedAccount,setLocalDemoAccess,startFreeDemo,submitAccessAuth,activatePremiumToken,logoutAccess,syncLocalDemoUsage,hasAppAccess,isLocalDemo,demoCanOpenPage,demoActionAllowed,decorateDemoLocks,render,navigate,startMission,submitMissionAnswer,completeMissionStep,missionAvailability,globalModeAccess,DEMO_MISSION_IDS,DEMO_QUESTION_IDS,missionById,libraryArticleById,AccessApi,startPremiumPayment,refreshPremiumPaymentState,claimPremiumPaymentToken,refreshPaymentConfig,formatRupiah};`;
  vm.runInContext(source, context, { filename: 'app.js', timeout: 15000 });
  return { qa: window.__eqQA, root: appRoot, window, localStorage, elements, api: accessApi };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  // 1–3: preserve existing profile, welcome flow/status, fixed demo bank, and premium-mode lock.
  const offlineApi = makeApi({ isConfigured: () => false, health: async () => ({ ok:false, code:'API_NOT_CONFIGURED' }) });
  const initial = makeHarness({
    profile: { completed: ['l1-1','l1-2'], xp: 500, settings: { theme: 'light', mode: 'medium', character: 'suki' } },
    api: offlineApi
  });
  await tick();
  const { qa, root } = initial;
  assert.deepEqual(Array.from(qa.profile.completed), ['l1-1','l1-2']);
  assert.equal(qa.profile.xp, 500);
  assert.match(root.innerHTML, /Coba Demo Gratis/);
  assert.match(root.innerHTML, /Aktivasi Premium/);
  assert.match(root.innerHTML, /Masuk \/ Buat Akun/);
  assert.match(root.innerHTML, /Server akun belum dikonfigurasi/);
  // Business rule: the public demo is five unique Easy questions (one complete Easy mission).
  const demoQuestions=['easy-greetings'].flatMap(id=>qa.missionById[id].questions.map(question=>question.id));
  assert.equal(demoQuestions.length,5,'the demo mission contains five questions');
  assert.equal(new Set(demoQuestions).size,5,'all demo question IDs are unique');
  assert.equal(qa.DEMO_QUESTION_IDS.size,5,'the quota bank contains exactly five stable IDs');
  assert.equal(qa.accessState.demoLimit,5,'the client mirrors the five-question server limit');
  assert.equal(qa.accessState.demoRemaining,5,'a fresh demo starts with five remaining questions');
  assert.ok(qa.DEMO_MISSION_IDS.has('easy-greetings')&&qa.DEMO_MISSION_IDS.size===1,'only the first Easy mission is demo content');
  qa.setVerifiedAccount(baseAccount());
  assert.equal(qa.globalModeAccess('medium').unlocked, false, 'retained local XP cannot unlock premium demo modes');
  assert.equal(qa.profile.completed.length, 2, 'connecting access must not clear local progress');
  assert.equal(qa.profile.xp, 500, 'local XP is preserved');
  assert.equal(qa.missionAvailability('easy', 0).available, true, 'the first complete Easy demo mission is available');
  assert.equal(qa.missionAvailability('easy', 1).available, false, 'the second Easy mission is Premium content');
  assert.equal(qa.missionAvailability('easy', 2).available, false, 'non-demo Easy missions are locked');

  // 4–5: direct premium routes are blocked; Home/settings/progress/activation stay reachable after quota.
  qa.setVerifiedAccount(baseAccount({ demo_limit:5, demo_used:5, demo_remaining:0, used_question_ids:Array.from(qa.DEMO_QUESTION_IDS) }));
  qa.state.missionPlay = null; qa.profile.activeMission = null; qa.state.page = 'mission-play'; qa.render();
  assert.equal(qa.state.page, 'premium-access', 'direct entry to a new question is guarded');
  assert.equal(qa.demoCanOpenPage('mission-play'), false);
  assert.equal(qa.demoCanOpenPage('home'), true);
  assert.equal(qa.demoCanOpenPage('stats'), true);
  assert.equal(qa.demoCanOpenPage('settings'), true);
  assert.equal(qa.demoCanOpenPage('premium-access'), true);
  assert.equal(qa.demoActionAllowed('start-mission', {dataset:{mode:'easy',id:'easy-greetings'}}), false);
  qa.state.page='home';qa.render();
  assert.match(root.innerHTML, /Fitur Premium/);
  assert.match(root.innerHTML, /Info \/ token Premium/);
  qa.navigate('stats');assert.equal(qa.state.page,'stats');
  qa.navigate('settings');assert.equal(qa.state.page,'settings');
  qa.navigate('story');assert.equal(qa.state.page,'premium-access','non-demo content remains locked at navigation time');

  // 6: a server-verified demo switched offline can continue with one device-only answer, never Premium.
  const serverQuestionApi=makeApi();let serverCalls=0;
  serverQuestionApi.consume=async qid=>{serverCalls++;return {ok:false,code:'NETWORK_ERROR',message:'offline'};};
  const serverDemo=makeHarness({api:serverQuestionApi});await tick();
  const serverQA=serverDemo.qa;
  serverQA.setVerifiedAccount(baseAccount({demo_used:1,demo_remaining:4,used_question_ids:['easy-greetings-q1']}));
  serverQA.profile.activeMission=null;
  assert.equal(serverQA.startMission('easy','easy-greetings'),true);
  assert.equal(serverQA.state.missionPlay.index,1,'a question already consumed on server is skipped');
  serverQA.state.missionPlay.selected='wrong-but-submitted';
  await serverQA.submitMissionAnswer();
  assert.equal(serverCalls,1);
  assert.equal(serverQA.isLocalDemo(),true,'network loss switches to restricted local fallback');
  assert.equal(serverQA.state.missionPlay.answered,true,'offline fallback counts the submitted answer once');
  assert.equal(serverQA.accessState.demoUsed,2);
  assert.equal(serverQA.accessState.demoRemaining,3);
  const qidAfterFallback=serverQA.profile.localDemoUsedQuestionIds.length;
  await serverQA.submitMissionAnswer();
  assert.equal(serverCalls,1,'duplicate submit never makes another request');
  assert.equal(serverQA.profile.localDemoUsedQuestionIds.length,qidAfterFallback,'duplicate submit never consumes a second question');
  assert.equal(serverQA.globalModeAccess('medium').unlocked,false,'offline fallback never grants Premium');

  // 7–8: no configured backend opens a local demo without auth; each unique submission counts once and survives restart.
  const local = makeHarness({api:makeApi({isConfigured:()=>false,health:async()=>({ok:false,code:'API_NOT_CONFIGURED'})})});await tick();
  await local.qa.startFreeDemo();
  assert.equal(local.qa.isLocalDemo(),true);
  assert.equal(local.qa.state.page,'home');
  const offlineStart=makeHarness({api:makeApi({isConfigured:()=>true,health:async()=>({ok:false,code:'NETWORK_ERROR',message:'Server tidak terjangkau'}),startDemo:async()=>({ok:false,code:'NETWORK_ERROR',message:'Server tidak terjangkau'})})});await tick();
  await offlineStart.qa.startFreeDemo();
  assert.equal(offlineStart.qa.isLocalDemo(),true,'a configured but unreachable API falls back to restricted local demo');
  assert.equal(offlineStart.qa.state.serverStatus,'offline');
  assert.match(offlineStart.qa.state.serverMessage,/tidak dapat dijangkau/);
  const limitedStart=makeHarness({api:makeApi({isConfigured:()=>true,startDemo:async()=>({ok:false,code:'RATE_LIMITED',message:'Terlalu banyak sesi demo baru.'})})});await tick();
  await limitedStart.qa.startFreeDemo();
  assert.equal(limitedStart.qa.isLocalDemo(),false,'a server rate limit is an explicit denial, not treated as a backend outage');
  assert.equal(limitedStart.qa.state.serverStatus,'limited');
  assert.match(limitedStart.root.innerHTML,/Server membatasi sesi demo baru/);
  assert.match(local.root.innerHTML,/DEMO LOKAL/);
  assert.match(local.root.innerHTML,/dapat ter-reset/);
  assert.equal(local.qa.startMission('easy','easy-greetings'),true);
  assert.equal(local.qa.accessState.demoUsed,0,'opening a mission does not consume quota');
  async function answerCurrent(qa){
    const play=qa.state.missionPlay,question=qa.missionById[play.missionId].questions[play.index];
    play.selected=question.answer;
    if(question.type==='order')play.orderSelected=question.words.map((_,index)=>index);
    await qa.submitMissionAnswer();
    assert.equal(play.answered,true);
    await qa.completeMissionStep();
  }
  for(let i=0;i<5;i++)await answerCurrent(local.qa);
  assert.equal(local.qa.accessState.demoUsed,5,'the whole demo quota is five unique questions');
  assert.equal(local.qa.accessState.demoRemaining,0);
  assert.equal(local.qa.profile.localDemoUsedQuestionIds.length,5);
  assert.equal(local.qa.missionAvailability('easy',1).available,false,'the second Easy mission belongs to Premium now');
  assert.equal(local.qa.startMission('easy','easy-alphabet-spelling'),false,'a Premium mission cannot be started in demo mode');
  assert.equal(local.qa.demoCanOpenPage('home'),true);
  assert.equal(local.qa.demoCanOpenPage('stats'),true);
  assert.equal(local.qa.demoCanOpenPage('premium-access'),true);
  assert.equal(local.qa.demoCanOpenPage('mission-play'),false);
  assert.equal(local.qa.startMission('easy','easy-numbers-time'),false,'another question cannot start after the fifth answer');
  local.qa.navigate('stats');assert.equal(local.qa.state.page,'stats');
  local.qa.navigate('settings');assert.equal(local.qa.state.page,'settings');
  local.qa.navigate('story');assert.equal(local.qa.state.page,'premium-access');
  const savedProfile=JSON.parse(local.localStorage.getItem('englishQuest.profile.v1'));
  const restarted=makeHarness({profile:savedProfile,api:makeApi({isConfigured:()=>false,health:async()=>({ok:false,code:'API_NOT_CONFIGURED'})})});await tick();
  await restarted.qa.startFreeDemo();
  assert.equal(restarted.qa.accessState.demoUsed,5,'local quota survives app restart through profile/native persistence');
  assert.equal(restarted.qa.accessState.demoRemaining,0);
  assert.equal(restarted.qa.startMission('easy','easy-greetings'),false,'used local questions are not replayable');

  // 9: a successful server submission is counted once and a retry is idempotent on the client.
  const successApi=makeApi();let accepted=0;successApi.consume=async qid=>{accepted++;return {ok:true,access_level:'demo',question_id:qid,consumed:true,duplicate:false,demo_used:1,demo_remaining:4};};
  const success=makeHarness({api:successApi});await tick();success.qa.setVerifiedAccount(baseAccount());
  assert.equal(success.qa.startMission('easy','easy-greetings'),true);
  success.qa.state.missionPlay.selected='Good morning!';
  await success.qa.submitMissionAnswer();
  assert.equal(accepted,1);assert.equal(success.qa.state.missionPlay.answered,true);
  assert.equal(success.qa.accessState.demoUsed,1);assert.equal(success.qa.accessState.demoRemaining,4);
  await success.qa.submitMissionAnswer();assert.equal(accepted,1,'submitting an already answered question is a no-op');

  // 10: guest demo entry and local-ledger sync do not require token/email and stay account-backed when online.
  let hasSession=false;let serverIds=[];
  const guestApi=makeApi({
    isConfigured:()=>true,hasSession:()=>hasSession,
    startDemo:async()=>{hasSession=true;return {ok:true,session_token:'opaque-test-session'};},
    me:async()=>({ok:true,account:baseAccount({anonymous:true,demo_used:serverIds.length,demo_remaining:5-serverIds.length,used_question_ids:serverIds})}),
    syncDemo:async ids=>{serverIds=[...new Set([...serverIds,...ids])];return {ok:true,imported:ids.length};}
  });
  const guest=makeHarness({api:guestApi});await tick();await guest.qa.startFreeDemo();
  assert.equal(guest.qa.accessState.user.anonymous,true);
  assert.equal(guest.qa.state.page,'home');
  assert.match(guest.root.innerHTML,/tanpa membuat akun|Demo tamu/i);
  const syncApi=makeApi();let syncedIds=[];
  syncApi.hasSession=()=>true;
  syncApi.me=async()=>({ok:true,account:baseAccount({demo_used:syncedIds.length,demo_remaining:5-syncedIds.length,used_question_ids:syncedIds})});
  syncApi.syncDemo=async ids=>{syncedIds=[...new Set([...syncedIds,...ids])];return {ok:true,imported:ids.length};};
  const syncHarness=makeHarness({profile:{localDemoUsedQuestionIds:['easy-greetings-q1','easy-greetings-q2']},api:syncApi});await tick();
  assert.deepEqual(syncedIds,['easy-greetings-q1','easy-greetings-q2']);
  assert.equal(syncHarness.qa.accessState.demoUsed,2);
  assert.equal(syncHarness.qa.accessState.demoRemaining,3);

  let authSession=false;let authAccount=baseAccount({email:'new-learner@example.test'});let activatedToken='';
  const authApi=makeApi({
    isConfigured:()=>true,hasSession:()=>authSession,
    register:async(email,password)=>{assert.equal(email,'new-learner@example.test');assert.equal(password,'correct-horse-battery');authSession=true;return {ok:true,session_token:'register-session'};},
    me:async()=>({ok:true,account:authAccount}),
    activate:async token=>{activatedToken=token;authAccount={...authAccount,premium:true};return {ok:true,activated:true};},
    syncDemo:async ids=>({ok:true,imported:ids.length})
  });
  const auth=makeHarness({profile:{completed:['l1-1'],xp:20},api:authApi});await tick();
  auth.qa.state.accessFlow='demo';auth.qa.state.authTab='register';auth.elements.set('#access-email',{value:'new-learner@example.test'});auth.elements.set('#access-password',{value:'correct-horse-battery'});
  await auth.qa.submitAccessAuth();
  assert.equal(auth.qa.state.page,'home','Create Account submits to the API and opens the verified account');
  assert.equal(auth.qa.accessState.user.email,'new-learner@example.test');
  assert.deepEqual(Array.from(auth.qa.profile.completed),['l1-1']);
  auth.qa.state.page='premium-access';auth.qa.render();auth.elements.set('#premium-token',{value:'EQP-'+ 'A'.repeat(48)});
  await auth.qa.activatePremiumToken();
  assert.equal(activatedToken,'EQP-'+ 'A'.repeat(48));
  assert.equal(auth.qa.hasAppAccess(),true);assert.equal(auth.qa.isLocalDemo(),false);
  assert.equal(auth.qa.accessState.mode,'premium','Premium is granted only after the API reports it');
  assert.deepEqual(Array.from(auth.qa.profile.completed),['l1-1'],'Premium activation does not clear learning progress');

  let loginSession=false;const loginApi=makeApi({isConfigured:()=>true,hasSession:()=>loginSession,login:async()=>{loginSession=true;return {ok:true,session_token:'login-session'};},me:async()=>({ok:true,account:baseAccount({email:'returning@example.test',demo_used:2,demo_remaining:3,used_question_ids:['easy-greetings-q1','easy-greetings-q2']})})});
  const login=makeHarness({api:loginApi});await tick();login.qa.state.accessFlow='demo';login.qa.state.authTab='login';login.elements.set('#access-email',{value:'returning@example.test'});login.elements.set('#access-password',{value:'saved-password'});
  await login.qa.submitAccessAuth();
  assert.equal(login.qa.state.page,'home','Login returns to the app after verifying the existing account');
  assert.equal(login.qa.accessState.demoRemaining,3);

  // Server role—not a hidden control—drives the admin link, and account progress remains intact.
  const adminApi=makeApi({getApiBaseUrl:()=> 'https://api.example.test'});
  const admin=makeHarness({profile:{completed:['l1-1'],xp:50},api:adminApi});await tick();
  admin.qa.setVerifiedAccount(baseAccount({user_id:'admin-1',email:'admin@example.test',role:'admin'}));
  admin.qa.state.page='home';admin.qa.render();
  assert.match(admin.root.innerHTML,/https:\/\/api\.example\.test\/admin/);
  assert.equal(admin.qa.profile.completed.length,1);
  assert.equal(admin.qa.profile.xp,50);

  // Runtime smoke the full route table with server-verified Premium to catch missing render helpers.
  const smoke=makeHarness({api:makeApi()});await tick();smoke.qa.setVerifiedAccount(baseAccount({premium:true}));
  const routes=['welcome','access-auth','access-loading','premium-access','demo-intro','home','learn','missions','mission-list','mission-play','mission-result','library','library-article','lesson','quiz','lesson-complete','level-exam','vocabulary','grammar','story','story-play','games','word-hunter','wordmatch','sentence-builder','picture-quiz','listening','sources','bule','anime-dialogue','rush','grammar-battle','npc-quest','chat-reply','practice','review-play','stats','creator','settings','suki-gate','suki-challenge','suki-story','more'];
  for(const page of routes){
    smoke.qa.state.lessonId='l1-1';smoke.qa.state.libraryArticleId=Object.keys(smoke.qa.libraryArticleById)[0]||'';
    smoke.qa.state.missionMode='easy';smoke.qa.state.missionResult={mode:'easy',missionId:'easy-greetings',passed:true,score:5,total:5,xpEarned:20,firstClear:true,streakClaimed:false,streakEligible:false};
    if(page==='mission-play')smoke.qa.startMission('easy','easy-greetings');
    smoke.qa.state.page=page;
    try{smoke.qa.render();}catch(error){throw new Error(`Route ${page} failed at runtime: ${error.stack||error}`);}
    assert.ok(smoke.root.innerHTML.length>100,`Route ${page} renders non-blank content`);
  }


  // 11: NEW product requirement replaces the old checkout/token-delivery UI.
  // Legacy API objects must not resurrect automatic checkout or claim a code.
  {
    let automatic=0;
    const forbidden=async()=>{automatic++;throw Error('automatic path called');};
    const buyer=makeHarness({api:makeApi({paymentConfig:forbidden,createPaymentOrder:forbidden,paymentOrder:forbidden,claimPaymentToken:forbidden})});
    await tick();buyer.qa.setVerifiedAccount(baseAccount());buyer.qa.state.page='premium-access';buyer.qa.render();
    await buyer.qa.refreshPaymentConfig();await buyer.qa.startPremiumPayment();await buyer.qa.refreshPremiumPaymentState();await buyer.qa.claimPremiumPaymentToken();
    assert.equal(automatic,0,'no legacy checkout/order/token route can run');
    assert.match(buyer.root.innerHTML,/Rp5.000/);assert.match(buyer.root.innerHTML,/permanen/);
    assert.match(buyer.root.innerHTML,/tanpa langganan/);assert.match(buyer.root.innerHTML,/KONFIRMASI PEMBAYARAN VIA WHATSAPP/);
    assert.doesNotMatch(buyer.root.innerHTML,/data-action="payment-start"|data-action="payment-claim"|checkout_url/);
    assert.ok(!buyer.qa.state.accessTokenInput);assert.equal(buyer.qa.accessState.mode==='premium',false);
    const guest=makeHarness({api:makeApi()});await tick();guest.qa.state.page='premium-access';guest.qa.render();
    assert.match(guest.root.innerHTML,/Hubungkan akun email/);assert.match(guest.root.innerHTML,/ENGLISH QUEST PREMIUM/);
  }
  console.log('Client gates: 11 acceptance groups passed (welcome/local fallback, full Easy demo, quota/restart, unique-submit, online guest/sync, premium fail-closed, progress, and server admin role); all 43 route renderers smoke-tested.');
})().catch(error => { console.error(error); process.exitCode = 1; });
