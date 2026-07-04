(() => {
  const state={token:'',engines:[],config:null,saved:new Set(),keyStatus:{},doc:null,timer:null};
  const id=(v)=>document.getElementById(v);
  const $={
    tabBtns:()=>Array.from(document.querySelectorAll('.tab-btn')),
    engine:id('engine-select'),googleModeWrap:id('google-mode-group'),googleMode:id('google-mode-select'),keyWrap:id('engine-keys'),
    fs:id('font-family'),fsV:id('font-size-value'),fsR:id('font-size'),fw:id('font-weight'),tc:id('color'),pc:id('partial-color'),bc:id('background-color'),ba:id('background-alpha'),baV:id('background-alpha-value'),ow:id('outline-width'),owV:id('outline-width-value'),oc:id('outline-color'),pos:id('position'),al:id('align'),ml:id('max-lines'),mlV:id('max-lines-value'),lh:id('line-height'),lhV:id('line-height-value'),pad:id('padding'),padV:id('padding-value'),ls:id('letter-spacing'),lsV:id('letter-spacing-value'),fm:id('fade-ms'),fmV:id('fade-ms-value'),up:id('uppercase'),mc:id('max-chars-per-line'),
    as:id('audio-source'),ad:id('audio-device'),sr:id('samplerate'),ch:id('channels'),
    sh:id('server-host'),sp:id('server-port'),oh:id('obs-host'),op:id('obs-port'),os:id('obs-source-name'),hkE:id('hotkey-enabled'),hkF:id('obs-hotkey-fields'),hkP:id('pause-input'),hkC:id('clear-input'),
    b1:id('save-engine-btn'),b2:id('save-keys-btn'),b3:id('save-style-btn'),b4:id('save-audio-btn'),b5:id('save-obs-btn'),frame:id('overlay-preview'),toast:id('toast')
  };
  const range=(inEl, outEl, f=(v)=>v)=>{const h=()=>outEl.textContent=f(inEl.value);inEl.addEventListener('input',h);h()};

  const err=(p)=> Array.isArray(p)&&p[0]&&typeof p[0]==='object'&&p[0].msg?`${p[0].loc?.join('.')??'validation'}: ${p[0].msg}`:typeof p==='string'?p:(p?.detail&&typeof p.detail==='string')?p.detail:'요청 처리 실패';
  const toast=(msg,kind='success')=>{const t=$.toast;t.textContent=msg;t.setAttribute('data-kind',kind);t.hidden=!1;if(state.timer!==null)clearTimeout(state.timer);state.timer=setTimeout(()=>t.hidden=!0,3e3)};

  const apiSession=async()=>{const r=await fetch('/api/session');if(!r.ok) throw new Error(`HTTP ${r.status}`);return r.json()};
  const api=async(path,{method='GET',body}= {})=>{const r=await fetch(path,{method,headers:{'Content-Type':'application/json',...(state.token&&path!=='/api/session'?{'X-OBS-Token':state.token}:{})},body});if(!r.ok){let p=null;try{p=await r.json()}catch{ }throw new Error(err(p||`HTTP ${r.status}`))}return r.json()};

  const toHex=(n)=>Math.max(0,Math.min(255,Number(n))).toString(16).padStart(2,'0').slice(-2);
  const rgb=(s)=>{const v=String(s||'rgba(0,0,0,0.35)');const a=v.match(/rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)/i),b=v.match(/rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/i);if(a)return{r:+a[1],g:+a[2],b:+a[3],a:+a[4]};if(b)return{r:+b[1],g:+b[2],b:+b[3],a:.35};return{r:0,g:0,b:0,a:.35}};
  const rgba=(r,g,b,a)=>`rgba(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)}, ${Number(a)})`;

  const getEngine=(v)=>state.engines.find((i)=>i.engine===v)||{};
  const keyState=(k)=> state.saved.has(k)?{c:'set',t:'저장됨✓'}:state.keyStatus[k]?{c:'set',t:'설정됨✓'}:{c:'missing',t:'미설정'};

  const syncBadges=()=>$.keyWrap.querySelectorAll('[data-key]').forEach((n)=>{const s=keyState(n.dataset.key);n.className=`badge badge-${s.c}`;n.textContent=s.t});

  const renderKeys=()=>{
    const m=getEngine($.engine.value);$.googleModeWrap.hidden=m.engine!=='google';const keys=m.engine==='google'?(m.modes?.[$.googleMode.value]?.env||[]):(m.env||[]);$.keyWrap.innerHTML='';
    if(!keys.length){const p=document.createElement('p');p.className='help';p.textContent='이 엔진은 별도 API 키가 필요하지 않습니다.';$.keyWrap.append(p);return;}
    for(const n of keys){const ks=keyState(n);const row=document.createElement('div');row.className='key-item';const h=document.createElement('div');h.className='row-head';const t=document.createElement('strong');t.textContent=n;const b=document.createElement('span');b.className=`badge badge-${ks.c}`;b.dataset.key=n;b.textContent=ks.t;const i=document.createElement('input');i.type='password';i.placeholder='비워두면 변경 없음';i.dataset.keyName=n;h.append(t,b);row.append(h,i);$.keyWrap.append(row)};
  };

  const renderOpts=()=>{renderEngine();renderKeys();syncBadges();
    const e=state.config.overlay;const bg=rgb(e.background);
    $.fs.value=e.font_family||"Pretendard, 'Noto Sans KR', sans-serif";$.fsR.value=e.font_size||48;$.fsV.textContent=`${e.font_size||48}px`;
    $.fw.value=String(e.font_weight||700);$.tc.value=e.color||'#ffffff';$.pc.value=e.partial_color||'#aaaaaa';$.bc.value=`#${toHex(bg.r)}${toHex(bg.g)}${toHex(bg.b)}`;$.ba.value=String(bg.a);$.baV.textContent=Number(bg.a).toFixed(2);$.ow.value=String(e.outline_width||2);$.owV.textContent=`${e.outline_width||2}px`;$.oc.value=e.outline_color||'#000000';$.pos.value=e.position||'bottom';$.al.value=e.align||'center';$.ml.value=String(e.max_lines||3);$.mlV.textContent=String(e.max_lines||3);$.lh.value=String(e.line_height||1.3);$.lhV.textContent=String(e.line_height||1.3);$.pad.value=String(e.padding||24);$.padV.textContent=String(e.padding||24);$.ls.value=String(e.letter_spacing||0);$.lsV.textContent=String(e.letter_spacing||0);$.fm.value=String(e.fade_ms||200);$.fmV.textContent=String(e.fade_ms||200);$.up.checked=Boolean(e.uppercase);$.mc.value=String(e.max_chars_per_line||0);

    const a=state.config.audio;$.as.value=a.source||'mic';$.ad.value=a.device||'';$.sr.value=String(a.samplerate||16000);$.ch.value=String(a.channels||1);
    const s=state.config.server;$.sh.value=s.host||'127.0.0.1';$.sh.disabled=!0;$.sp.value=String(s.port||8765);
    const o=state.config.obs;$.oh.value=o.host||'localhost';$.op.value=String(o.port||4455);$.os.value=o.source_name||'LiveCaptions';const h=o.hotkey||{};$.hkE.checked=Boolean(h.enabled);$.hkP.value=h.pause_input||'_CaptionPause';$.hkC.value=h.clear_input||'_CaptionClear';$.hkF.hidden=!$.hkE.checked;
  };

  const renderEngine=()=>{
    $.engine.innerHTML='';
    state.engines.forEach((i)=>{
      const o=document.createElement('option');
      o.value=i.engine;
      o.textContent=i.label;
      $.engine.append(o);
    });
    if (state.config?.engine) $.engine.value = state.config.engine;
  };

  const collectOverlay=()=>{
    const bg=rgb(`rgba(0,0,0,${Number($.ba.value)})`);
    const r=parseInt($.bc.value.slice(1, 3), 16);
    const g=parseInt($.bc.value.slice(3, 5), 16);
    const b=parseInt($.bc.value.slice(5, 7), 16);
    return {
      font_family: $.fs.value.trim() || "Pretendard, 'Noto Sans KR', sans-serif",
      font_size: Number($.fsR.value ? $.fsR.value : 48),
      font_weight: Number($.fw.value),
      color: $.tc.value,
      partial_color: $.pc.value,
      background: rgba(r, g, b, Number($.ba.value)),
      outline_width: Number($.ow.value),
      outline_color: $.oc.value,
      position: $.pos.value,
      align: $.al.value,
      max_lines: Number($.ml.value),
      line_height: Number($.lh.value),
      padding: Number($.pad.value),
      letter_spacing: Number($.ls.value),
      fade_ms: Number($.fm.value),
      uppercase: $.up.checked,
      max_chars_per_line: Number($.mc.value),
    };
  };

  const collectAudio=()=>({
    source: $.as.value,
    device: $.ad.value.trim() || null,
    samplerate: Number($.sr.value),
    channels: Number($.ch.value),
  });

  const collectObs=()=>({
    host: $.oh.value.trim() || 'localhost',
    port: Number($.op.value),
    source_name: $.os.value.trim() || 'LiveCaptions',
    hotkey: {
      enabled: Boolean($.hkE.checked),
      pause_input: $.hkP.value.trim() || '_CaptionPause',
      clear_input: $.hkC.value.trim() || '_CaptionClear',
    },
  });

  const seedPreview=()=>{if(!state.doc)return;const c=state.doc.querySelector('.committed');const p=state.doc.querySelector('.partial');const cap=state.doc.querySelector('.caption');if(c)c.textContent='이곳은 오버레이 미리보기 박스입니다.';if(p)p.textContent='실시간 자막 스타일이 즉시 반영됩니다.';if(cap)cap.dataset.empty='false';const l=state.doc.querySelector('link[href*="/overlay-style.css"]');if(l)l.href=`/overlay-style.css?t=${Date.now()}`};
  const withFresh=async(fn)=>{const c=await api('/api/config',{method:'GET'});const n=JSON.parse(JSON.stringify(c));fn(n);['openai_api_key','elevenlabs_api_key','openrouter_api_key','replicate_api_token','xai_api_key','gemini_api_key'].forEach((k)=>{delete n[k]});return api('/api/config',{method:'POST',body:JSON.stringify(n)});};

  const saveEngine=async()=>{
    try {
      await withFresh((n) => {
        n.engine = $.engine.value;
        n.providers = n.providers || {};
        if (n.engine === 'google') {
          n.providers.google = { ...(n.providers.google || {}), mode: $.googleMode.value };
        }
      });
      state.config = await api('/api/config', { method: 'GET' });
      renderOpts();
      toast('저장되었습니다');
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  const saveKeys=async()=>{
    const p={};
    $.keyWrap.querySelectorAll('input[data-key-name]').forEach((i)=>{
      const value=String(i.value || '').trim();
      if (value) p[i.dataset.keyName] = value;
    });
    if (!Object.keys(p).length) {
      toast('입력된 키가 없습니다.', 'error');
      return;
    }
    try {
      const r = await api('/api/keys', { method: 'POST', body: JSON.stringify(p) });
      Object.entries(r).forEach(([name, ok]) => {
        if (ok) state.saved.add(name);
      });
      syncBadges();
      $.keyWrap.querySelectorAll('input[data-key-name]').forEach((i) => {
        i.value = '';
      });
      toast('저장되었습니다');
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  const saveStyle=async()=>{
    try {
      await withFresh((n)=>{ n.overlay = { ...n.overlay, ...collectOverlay() }; });
      state.config = await api('/api/config', { method: 'GET' });
      $.frame.src = `/overlay.html?t=${Date.now()}`;
      toast('저장되었습니다');
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  const saveAudio=async()=>{
    try {
      await withFresh((n)=>{ n.audio = { ...n.audio, ...collectAudio() }; });
      state.config = await api('/api/config', { method: 'GET' });
      toast('저장되었습니다');
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  const saveObs=async()=>{
    try {
      await withFresh((n)=>{
        n.server = {
          ...(n.server || {}),
          host: (n.server || {}).host || '127.0.0.1',
          port: Number($.sp.value),
        };
        n.obs = { ...n.obs, ...collectObs() };
      });
      state.config = await api('/api/config', { method: 'GET' });
      renderOpts();
      toast('저장되었습니다');
    } catch (e) {
      toast(e.message, 'error');
    }
  };

  const setup=()=>{
    range($.fsR,$.fsV,(v)=>`${v}px`);range($.ba,$.baV,(v)=>Number(v).toFixed(2));range($.ow,$.owV,(v)=>`${v}px`);range($.ml,$.mlV);range($.lh,$.lhV);range($.pad,$.padV);range($.ls,$.lsV);range($.fm,$.fmV,(v)=>`${v}ms`);
    $.tabBtns().forEach((b)=>b.addEventListener('click',()=>{$.tabBtns().forEach((x)=>x.classList.remove('is-active'));Object.values(document.querySelectorAll('.tab-panel')).forEach((p)=>p.classList.remove('is-active'));b.classList.add('is-active');id(`tab-${b.dataset.tab}`).classList.add('is-active')}));
    $.engine.addEventListener('change',renderKeys);$.googleMode.addEventListener('change',renderKeys);$.hkE.addEventListener('change',()=>{$.hkF.hidden=!$.hkE.checked});
    $.b1.addEventListener('click',saveEngine);$.b2.addEventListener('click',saveKeys);$.b3.addEventListener('click',saveStyle);$.b4.addEventListener('click',saveAudio);$.b5.addEventListener('click',saveObs);
    $.frame.addEventListener('load',()=>{try{state.doc=$.frame.contentDocument}catch(e){state.doc=null}seedPreview()});
  };

  const init=async()=>{setup();try{const s=await apiSession();state.token=s.token;const [eng,cfg,keyStatus]=await Promise.all([api('/api/engines',{method:'GET'}),api('/api/config',{method:'GET'}),api('/api/keys/status',{method:'GET'})]);state.engines=eng;state.config=cfg;state.keyStatus=keyStatus;renderOpts();if(getEngine(state.config.engine).engine==='google')$.googleMode.value=(state.config.providers?.google&&state.config.providers.google.mode)||'gemini';renderEngine();renderKeys();if(cfg?.providers?.google?.mode)$.googleMode.value=cfg.providers.google.mode;$.frame.src=`/overlay.html?t=${Date.now()}`;}catch(e){toast(e.message,'error')}};

  init();
})();
