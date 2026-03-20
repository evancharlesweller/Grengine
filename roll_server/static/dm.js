(function(){
  const $ = (id)=>document.getElementById(id);

  function fmtTs(ts){
    try{
      const d = new Date((ts||0)*1000);
      if (isNaN(d.getTime())) return '';
      return d.toLocaleString();
    }catch(e){ return ''; }
  }

  function summarize(e){
    try{
      const t = (e.type||'').toString();
      if (t === 'ROLL_REQUESTED'){
        const exp = e.expected_sides ? `d${e.expected_sides}` : '';
        const cnt = (e.expected_count_min===e.expected_count_max) ? `${e.expected_count_min}` : `${e.expected_count_min}-${e.expected_count_max}`;
        const dc = (e.dc!==null && e.dc!==undefined) ? ` DC ${e.dc}` : '';
        return `${e.roll_kind||''} ${cnt}×${exp}${dc} ${e.label||''}`.trim();
      }
      if (t === 'ROLL_SUBMITTED'){
        const dc = (e.dc!==null && e.dc!==undefined) ? ` vs DC ${e.dc}` : '';
        return `${e.roll_kind||''} d${e.die_sides||''} rolls=${JSON.stringify(e.rolls||[])} chosen=${e.chosen}${dc} ${e.label||''}`.trim();
      }
      if (t === 'ATTACK_TO_HIT_SUBMITTED'){
        return `pending=${(e.pending_attack_id||'').slice(0,8)} mode=${e.mode||''} rolls=${JSON.stringify(e.rolls||[])}`;
      }
      if (t === 'DAMAGE_ROLL_SUBMITTED'){
        return `attack=${(e.attack_id||'').slice(0,8)} damage=${JSON.stringify(e.damage_roll||{})}`;
      }
      if (t === 'ATTACK_RESULT_POSTED'){
        return `${e.attacker_name||''} -> ${e.target_name||''} roll=${e.roll||''} total=${e.total||''} vs AC ${e.ac||''} ${e.result||''} dmg=${e.damage||''}`;
      }
      return JSON.stringify(e);
    }catch(err){
      return '';
    }
  }

  async function apiGet(url){
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  }

  function buildUrl(){
    const campaign = ($('campaignId').value||'Test').trim();
    const pid = ($('playerId').value||'').trim();
    const cid = ($('characterId').value||'').trim();
    const q = ($('q').value||'').trim();
    const limit = Math.max(1, Math.min(2000, parseInt($('limit').value||'200',10)));
    const params = new URLSearchParams();
    if (pid) params.set('player_id', pid);
    if (cid) params.set('character_id', cid);
    if (q) params.set('q', q);
    params.set('limit', String(limit));
    const url = `/api/campaigns/${encodeURIComponent(campaign)}/dm/roll_audit?` + params.toString();
    $('exportLink').href = `/api/campaigns/${encodeURIComponent(campaign)}/dm/roll_audit/export`;
    return { url, campaign };
  }

  async function refresh(){
    const { url, campaign } = buildUrl();
    $('meta').textContent = 'Loading...';
    let data;
    try{
      data = await apiGet(url);
    }catch(e){
      $('meta').textContent = `Error loading audit for campaign '${campaign}'`;
      $('tbody').innerHTML = '';
      return;
    }
    const evs = (data && data.events) ? data.events : [];
    $('meta').textContent = `${evs.length} event(s)`;
    const rows = evs.map(e=>{
      const ts = e.ts || e.received_at || e.created_at || 0;
      return `<tr>
        <td>${fmtTs(ts)}</td>
        <td>${(e.type||'')}</td>
        <td>${(e.player_id||'')}</td>
        <td>${(e.character_id||'')}</td>
        <td><pre style="margin:0; white-space:pre-wrap;">${escapeHtml(summarize(e))}</pre></td>
      </tr>`;
    }).join('');
    $('tbody').innerHTML = rows;
  }

  function escapeHtml(s){
    return (s||'').toString()
      .replaceAll('&','&amp;')
      .replaceAll('<','&lt;')
      .replaceAll('>','&gt;');
  }

  let auto = false;
  let timer = null;

  function setAuto(on){
    auto = !!on;
    $('autoBtn').textContent = auto ? 'Auto: On' : 'Auto: Off';
    if (timer) { clearInterval(timer); timer=null; }
    if (auto){
      timer = setInterval(refresh, 1500);
    }
  }

  async function initCampaignDefault(){
    try{
      const d = await apiGet('/api/campaigns');
      const def = (d && d.default) ? d.default : 'Test';
      $('campaignId').value = def;
    }catch(e){
      $('campaignId').value = 'Test';
    }
  }

  $('refreshBtn').addEventListener('click', refresh);
  $('autoBtn').addEventListener('click', ()=> setAuto(!auto));

  initCampaignDefault().then(refresh);
})();