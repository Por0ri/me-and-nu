const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const root = path.join(__dirname, '..');
const modules = new Map();
function load(relative) {
  if (modules.has(relative)) return modules.get(relative).exports;
  if (relative.endsWith('.json')) return JSON.parse(fs.readFileSync(path.join(root, relative), 'utf8'));
  const mod = { exports: {} };
  modules.set(relative, mod);
  const code = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  vm.runInNewContext(code, { module: mod, exports: mod.exports, setTimeout, clearTimeout, URL, URLSearchParams, AbortController, process:{env:{}}, require(name) {
    if (name === 'react' || name === 'react/jsx-runtime') return require(name);
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    if (name.startsWith('@/')) return load(name.slice(2) + (name.endsWith('.json') ? '' : '.ts'));
    if (name.startsWith('./') || name.startsWith('../')) {
      const base = path.join(path.dirname(relative), name);
      return load(base + (fs.existsSync(path.join(root, base + '.tsx')) ? '.tsx' : '.ts'));
    }
    throw new Error('Unexpected onboarding UI dependency: ' + name);
  } });
  return mod.exports;
}
const { OnboardingFlow, InterestScreen } = load('components/pivot/onboarding-screens.tsx');
const { PivotFrame } = load('components/pivot/pivot-ui.tsx');
const { OnboardingProfileScreen } = load('components/pivot/onboarding-profile.tsx');
const { initialOnboardingState } = load('lib/pivot/onboarding.ts');
function render(step, mode, answers = {}) {
  return renderToStaticMarkup(React.createElement(OnboardingFlow, {
    state: { ...initialOnboardingState(), step, interest: 'animation', topicId:83,
      favoriteWorks:[{entityId:101,name:'테스트 작품',aliases:[],imageUrl:null,releaseYear:2024,mediaFormat:'TV',selectable:true,unavailableReason:null}],
      preferenceOptions:[{tagId:201,kind:'genre',code:'fantasy',label:'판타지',sourceEntityIds:[101]},{tagId:301,kind:'theme',code:'friendship',label:'우정',sourceEntityIds:[101]}],
      preferenceTagIds:[201,301],optionsKey:'101',consumptionTypes:['ost'],...answers },
    dispatch() {}, onExit() {}, mode,
  }));
}
test('My Page 관심분야 관리: actual subscription state, back button, no checkbox or start button', () => {
  for (const selected of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(InterestScreen, { selected, onToggle() {}, onNext() {}, busy: false, manage: { onBack() {} } }));
    assert.match(html, /aria-label="뒤로가기"/);
    assert.match(html, selected ? />선택중</ : />미가입</);
    assert.match(html, selected ? /이미 애니메이션을 탐색중입니다/ : /가입된 애니메이션 관심 분야가 없어요\./);
    assert.doesNotMatch(html, selected ? /미가입|가입된 애니메이션 관심 분야가 없어요/ : /선택중|이미 애니메이션을 탐색중입니다/);
    assert.doesNotMatch(html, /type="checkbox"|애니메이션 선택하고 시작하기/);
    assert.match(html, /alt="me;nu" width="103"/);
    assert.match(html, /Little discoveries<br\/>for a more you/);
    assert.match(html, /aria-label="알림"/);
  }
  const onboarding = renderToStaticMarkup(React.createElement(InterestScreen, { selected: true, onToggle() {}, onNext() {}, busy: false }));
  assert.match(onboarding, /선택 완료/); assert.match(onboarding, /애니메이션 선택하고 시작하기/); assert.doesNotMatch(onboarding, /뒤로가기/);
});
for (const mode of ['demo', 'service']) {
  test(mode + ': interest/result omit ordinary Back; questions retain it', () => {
    for (const step of ['interest', 'result']) assert.doesNotMatch(render(step, mode), /aria-label="이전 단계"/);
    for (const step of ['q1', 'q2','q3']) assert.equal((render(step, mode).match(/aria-label="이전 단계"/g) || []).length, 1);
    assert.match(render('result', mode), />다시 선택하기<\/button>/);
  });
  test(mode + ': Q1 selected works and every result chip row slide with a mouse drag', () => {
    assert.match(render('q1', mode), /<ul class="selectedWorks" aria-label="선택한 작품" data-drag-scroll="">/);
    assert.equal((render('result', mode).match(/<ul class="summaryChips" data-drag-scroll="">/g) || []).length, 4);
  });
  test(mode + ': Q2 explains the per-kind minimum and an incomplete result cannot be submitted', () => {
    assert.match(render('q2', mode), /장르·소재 각각 최소 1개/);
    for (const ids of [[], [201], [301]]) {
      const html = render('result', mode, {preferenceTagIds: ids});
      assert.match(html, /<button[^>]*disabled=""[^>]*aria-describedby="home-feedback"/);
    }
  });
  test(mode + ': unanswered Q1/Q2/Q3 block CTA and contain no stale result summary', () => {
    for (const step of ['q1', 'q2','q3']) {
      const html = render(step, mode, { favoriteWorks:[],preferenceTagIds:[],preferenceOptions:[],optionsKey:null,consumptionTypes:[] });
      assert.match(html, /<button[^>]*disabled=""[^>]*>(?:선택 완료|다음|장르·소재 선택 완료)<\/button>/);
      assert.doesNotMatch(html, /선택 답변 요약/);
      assert.match(html,/aria-valuemax="3"/);
    }
  });
}
test('Q3 is six checkboxes that show the tap order as rank badges and say the rank in their names', () => {
  const html = render('q3', 'service', { consumptionTypes:['news','world','ost'] });
  assert.match(html, /<span>Q3\.<\/span>주로 어떤 정보를 찾나요\?/);
  assert.match(html, /가장 많이 찾는 것 순서대로 선택해 주세요<br\/>1개만 선택해도 되고 6개 다 선택해도 됩니다/);
  assert.match(html, /<legend class="srOnly">Q3 주로 찾는 정보를 많이 찾는 순서대로 최대 6개 선택<\/legend>/);
  assert.doesNotMatch(html, /type="radio"/);
  const attribute = (tag, name) => tag.match(new RegExp(` ${name}="([^"]*)"`))?.[1];
  const inputs = [...html.matchAll(/<input [^>]*name="consumptionTypes"[^>]*>/g)].map(([tag]) => {
    assert.equal(attribute(tag, 'type'), 'checkbox');
    return [attribute(tag, 'value'), attribute(tag, 'aria-label'), attribute(tag, 'checked') !== undefined];
  });
  assert.deepEqual(inputs, [
    ['world', '설정•세계관과 해석을 파고든다, 2순위', true],
    ['relationships', '캐릭터와 성우 이야기에 빠진다', false],
    ['ost', '관련 OST를 찾아듣는다, 3순위', true],
    ['goods', '굿즈를 모으거나 구매 정보를 찾는다', false],
    ['events', '팝업•전시•이벤트 등을 찾아간다', false],
    ['news', '새 소식과 재밌는 글을 챙겨본다, 1순위', true],
  ]);
  // One badge per chosen card, in card order, hidden from screen readers (the checkbox name says the rank).
  assert.deepEqual([...html.matchAll(/<span class="rank" aria-hidden="true" data-rank="(\d)">(\d)<\/span>/g)].map(([, rank, text]) => [rank, text]), [['2', '2'], ['3', '3'], ['1', '1']]);
  assert.equal((html.match(/class="choice q1Card selected"/g) || []).length, 3);
  assert.match(html, /<button[^>]*>선택 완료<\/button>/);
  assert.doesNotMatch(html, /<button[^>]*disabled=""[^>]*>선택 완료<\/button>/);
  assert.match(html, /<span>새 소식과 재밌는<br\/>글을 챙겨본다<\/span>/);
  const all = render('q3', 'service', { consumptionTypes:['events','goods','ost','relationships','world','news'] });
  assert.deepEqual([...all.matchAll(/data-rank="(\d)"/g)].map(match => match[1]), ['5', '4', '3', '2', '1', '6']);
  const none = render('q3', 'service', { consumptionTypes:[] });
  assert.doesNotMatch(none, /data-rank|순위|checked=""| selected"/);
  assert.match(none, /<button[^>]*disabled=""[^>]*>선택 완료<\/button>/);
});

test('The result lists every Q3 answer in rank order without rank numbers', () => {
  for (const mode of ['demo', 'service']) {
    const html = render('result', mode, { consumptionTypes:['news','world','ost'] });
    const row = html.slice(html.indexOf('주로 찾는 정보'), html.indexOf('</ul>', html.indexOf('주로 찾는 정보')));
    assert.deepEqual([...row.matchAll(/<li class="answerTag">([^<]+)<\/li>/g)].map(match => match[1]), ['새 소식 · 재밌는 글', '설정 · 세계관', '관련 OST'], mode);
    assert.doesNotMatch(row, /순위|[1-6]/, mode);
  }
  // Demo has no submission to wait for: a complete ranking leaves the button on.
  assert.doesNotMatch(render('result', 'demo', { consumptionTypes:['news','world','ost'] }), /<button[^>]*disabled=""[^>]*aria-describedby="home-feedback"/);
  assert.match(render('result', 'demo', { consumptionTypes:[] }), /<button[^>]*disabled=""[^>]*aria-describedby="home-feedback"/);
});

test('Web frame omits simulated OS status indicators even in design preview', () => {
  const html = renderToStaticMarkup(React.createElement(PivotFrame, { chrome: true }, 'Screen'));
  assert.doesNotMatch(html, /9:41|imgWifi|imgOutline|imgIconMobileSignal|imgBatteryEnd/);
});

test('Deferred birth-date policy exposes only the nickname input in the new onboarding profile',()=>{
  const html=renderToStaticMarkup(React.createElement(OnboardingProfileScreen,{profile:{nickname:''},onChange(){},onContinue(){}}));
  assert.match(html,/name="nickname"/);
  assert.doesNotMatch(html,/birthDate|type="date"|만 14세|autoComplete="bday"/);
});

test('Q2 puts genres and themes in the design order and keeps codes it does not list after them in server order',()=>{
  const { designChipOrder } = load('components/pivot/onboarding-catalog-screens.tsx');
  // The server sends model/catalog order (most common first); two codes the design does not know come last.
  const genres=['new_b','comedy','action','drama','romance','fantasy','slice_of_life','adventure','supernatural','sci_fi','mystery','psychological','sports','horror','mecha','music','thriller','new_a'];
  assert.deepEqual(Array.from(designChipOrder(genres.map(code=>({kind:'genre',code}))),tag=>tag.code),
    ['comedy','action','drama','romance','sci_fi','fantasy','slice_of_life','adventure','supernatural','mecha','mystery','psychological','sports','horror','music','thriller','new_b','new_a']);
  const themes=['school','tragedy','magic','coming_of_age','swordplay','harem','super_power','gore'];
  assert.deepEqual(Array.from(designChipOrder(themes.map(code=>({kind:'theme',code}))),tag=>tag.code),
    ['school','tragedy','magic','coming_of_age','swordplay','super_power','harem','gore']);
});

test('Q2 box shows the chips the chosen works carry, most shared first, three per kind',()=>{
  const { discoveredTags } = load('components/pivot/onboarding-catalog-screens.tsx');
  const tag=(tagId,kind,ids)=>({tagId,kind,code:String(tagId),label:'t'+tagId,sourceEntityIds:ids});
  const options=[tag(1,'genre',[101]),tag(2,'genre',[]),tag(3,'genre',[101,102]),tag(4,'genre',[102]),tag(5,'genre',[101]),tag(6,'theme',[101])];
  assert.deepEqual(Array.from(discoveredTags(options,'genre'),t=>t.tagId),[3,1,4]);
  assert.deepEqual(Array.from(discoveredTags(options,'theme'),t=>t.tagId),[6]);
  assert.equal(discoveredTags(options.map(t=>({...t,sourceEntityIds:[]})),'genre').length,0);
});

test('Feed loading screen uses the teammate artwork with the design words',()=>{
  const { FeedLoadingScreen } = load('components/pivot/feed-loading-screen.tsx');
  const html=renderToStaticMarkup(React.createElement(FeedLoadingScreen));
  assert.match(html,/취향을 불러오는 중/);
  assert.match(html,/맞춤 콘텐츠를 수집하고 있어요/);
  assert.match(html,/\/pivot\/loading\/phase-1\.png/);
  assert.match(html,/\/pivot\/loading\/dots-1\.svg/);
  assert.doesNotMatch(html,/시연용|처음부터 시연/);
});

test('Nickname screen waits for the duplicate check and shows a taken nickname in red',()=>{
  const screen=props=>renderToStaticMarkup(React.createElement(OnboardingProfileScreen,{onChange(){},onContinue(){},...props}));
  // A valid nickname still waits (grey) until the check answers; without a check it can start at once.
  assert.match(screen({profile:{nickname:'미뉴덕후'},checkNickname:async()=>({available:true,reason:null})}),/disabled=""[^>]*>시작하기/);
  assert.doesNotMatch(screen({profile:{nickname:'미뉴덕후'}}),/disabled=""/);
  const taken=screen({profile:{nickname:'공룡'},message:'중복된 닉네임입니다'});
  assert.match(taken,/role="alert">중복된 닉네임입니다</);
  assert.match(taken,/aria-invalid="true"/);
});

test('Nickname screen marks each broken part of the 2~20 rule and starts only with a valid nickname',()=>{
  const render=nickname=>renderToStaticMarkup(React.createElement(OnboardingProfileScreen,{profile:{nickname},onChange(){},onContinue(){}}));
  const red=part=>new RegExp(`<span class="profileRuleBroken">${part}</span>`);
  const empty=render('');
  assert.match(empty.replace(/<[^>]+>/g,''),/2~20자, 공백·특수문자 제외/);
  assert.match(empty,/>0\/20</);
  assert.match(empty,/<button type="submit"[^>]*disabled=""[^>]*>시작하기<\/button>/);
  assert.doesNotMatch(empty,/profileRuleBroken|약관으로 돌아가기|취향 선택하기|1~30자/);
  const valid=render('미뉴덕후');
  assert.match(valid,/>4\/20</);
  assert.doesNotMatch(valid,/disabled=""|profileRuleBroken/);
  for (const [nickname,parts] of [['미',['2~20자']],['가'.repeat(21),['2~20자']],['미뉴 덕후',['공백·특수문자 제외']],['킹갓디노 ',['공백·특수문자 제외']],['!',['2~20자','공백·특수문자 제외']]]) {
    const html=render(nickname);
    assert.match(html,/disabled=""/,nickname);
    for (const part of ['2~20자','공백·특수문자 제외']) (parts.includes(part)?assert.match:assert.doesNotMatch)(html,red(part),`${nickname}: ${part}`);
  }
});

