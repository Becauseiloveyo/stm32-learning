'use strict';
const cases = JSON.parse(document.getElementById('replay-data').textContent).cases;
const byId = id => document.getElementById(id);
const show = (id, value) => { byId(id).textContent = value; };
const names = {
  wet_connected: '受水 · 正常联网', wet_reconnect: '受水 · 断网后重连',
  change_offline: '断网期间换衣', boot_offline: '断网期间衣架重启',
  ack_lost: '确认丢失后重发', manual_charge: '低电量 · 间歇充电',
  no_charge: '低电量 · 无人充电', missing_packet: '观测缺包', normal_cycle: '完整测量周期',
  forecast_boundary: '预报离线生效与过期', forecast_wet: '预报未报风险 · 探头受水',
  contact_missing: '中央接触丢失', queue_full: '离线历史队列已满'
};
const contact = {confirmed:'已确认', missing:'未接触', unknown:'未知', unavailable:'未知'};
const wet = {wet:'已受水', dry:'当前干燥', unavailable:'无有效读数', unknown:'未知'};
const forecast = {risk:'当前有降雨风险', upcoming_risk:'稍后有降雨风险', no_risk:'当前未报风险',
  upcoming_no_risk:'稍后未报风险', unavailable:'无有效预报', unknown:'未知', upcoming_unknown:'稍后风险未知'};
const heat = {clear:'已排除干扰', interference:'受充电干扰', unavailable:'未知', unknown:'未知'};
const reasons = {initial_no_prediction:'尚未取得有效预测。', local_wet:'探头受水，先前的干燥预测已撤销。',
  contact_missing:'中央未接触，无法记录这件衣物的干燥过程。', contact_unknown:'中央接触情况未知。',
  heat_interference:'充电热量可能影响观测，预测已暂停。', heat_unknown:'等待新的热状态证据。',
  local_unavailable:'雨头读数或暴露条件未知。', identity_changed:'启动、衣物或安装标识已改变，需重新取证。',
  fault:'设备有故障，原预测已撤销。', prediction_expired:'预测证据已过期。',
  heat_expired:'热状态证据已过期。', contact_expired:'中央接触证据已过期。',
  local_expired:'雨头证据已过期。', evidence_expired:'计算所需的证据已过期。', m6_unavailable:'计算模型暂不可用。'};
const actionNames = {
  state_saved:'现场状态已入队', boot_state_saved:'重启后的未知状态已入队',
  local_sample:'读取本地雨头', observation_paid:'取得观测包', observation_unavailable:'未取得观测包',
  sample_denied:'采样电量不足', radio_start:'开始无线连接', radio_timeout:'连接超时',
  radio_denied:'电量不足，推迟联网', server_saved:'服务已保存', simulated_receiver:'模拟端已接收',
  device_ack:'衣架收到确认', receiver_query:'查询远端记录', pc_analysis:'完成条件预测计算',
  analysis_missing_sample:'分析发现缺测', analysis_ineligible:'条件不满足，跳过分析',
  analysis_obsolete:'上传时证据已失效', measurement_start:'开始测量', measurement_denied:'未启动测量',
  measurement_cancelled:'中止测量并安全退出', measurement_budget_complete:'测量周期结束'
};
function stamp(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  const digits = n => String(n).padStart(2, '0');
  return digits(Math.floor(seconds / 60)) + ':' + digits(Math.floor(seconds % 60));
}
function duration(s) {
  const minutes = Math.floor(s / 60);
  const seconds = Math.round((s % 60) * 10) / 10;
  return (minutes ? minutes + ' 分 ' : '') + seconds + ' 秒';
}
function hasEstimate(s) {
  return s && s.prediction === 'conditional' && Number.isFinite(s.remaining_s);
}
function estimate(s) {
  if (hasEstimate(s)) return duration(s.remaining_s);
  if (s.prediction === 'coverage_or_model_mismatch') return '观测与模型不匹配';
  if (s.prediction === 'unresolved' || s.prediction === 'conditional') return '上界未知';
  return '暂无有效估计';
}
function note(s) {
  if (hasEstimate(s)) return '分析时刻起，在给定条件下达到模型目标的网格剩余时间上界。';
  if (s.prediction === 'coverage_or_model_mismatch') return '当前观测超出候选模型覆盖范围。';
  if (s.prediction === 'unresolved' || s.prediction === 'conditional') return '现有观测和条件无法给出有限的剩余时间上界。';
  return reasons[s.reason] || '当前缺少有效预测证据。';
}
function title(s) {
  if (s.wet === 'wet') return '探头检测到受水';
  if (s.contact === 'missing') return '衣物未接触横杆中央';
  if (s.heat === 'interference') return '充电中，暂停预测';
  if (s.reason === 'fault') return '设备故障，暂停预测';
  if (s.prediction === 'conditional' && hasEstimate(s)) return '已有条件预测';
  if (s.prediction === 'coverage_or_model_mismatch') return '观测与模型不匹配';
  if (s.prediction === 'unresolved') return '干燥时间仍未知';
  if (['risk','upcoming_risk'].includes(s.forecast)) return '预报提示降雨风险';
  return '等待有效观测';
}
function detail(s) {
  if (s.wet === 'wet') return '受水发生在探头表面。雨、露水和喷溅都可能产生这条读数。';
  if (['risk', 'upcoming_risk'].includes(s.forecast)) return '天气预报提供风险线索，探头读数独立显示。';
  return note(s);
}
const picker = byId('case');
const slider = byId('frame');
for (const c of cases) picker.add(new Option(names[c.id] || c.title || c.id, c.id));
let current;
function render(index) {
  const f = current.frames[index];
  const s = f.local;
  const r = f.remote;
  slider.value = index;
  slider.setAttribute('aria-valuetext', '第 ' + f.t + ' 秒，第 ' + (index+1) + ' 个关键帧');
  show('clock', stamp(f.t));
  show('frame-count', (index+1) + ' / ' + current.frames.length);
  byId('prev').disabled = index === 0;
  byId('next').disabled = index === current.frames.length-1;
  show('headline', title(s)); show('explain', detail(s));
  show('contact', contact[s.contact] || '未知'); show('wet', wet[s.wet] || '未知');
  show('forecast', forecast[s.forecast] || '未知'); show('heat', heat[s.heat] || '未知');
  show('estimate', estimate(s)); show('prediction-note', note(s));
  show('analysis', stamp(s.analysis_s)); show('expiry', stamp(s.expires_s));
  show('conditions', (Array.isArray(s.conditions) ? s.conditions.join('；') : s.conditions) || '—');
  show('remote-title', r ? title(r) : '尚无同步记录');
  const remoteText = !r ? '等待衣架首次成功同步。' : hasEstimate(r)
    ? '分析于 ' + stamp(r.analysis_s) + '，当时的条件上界为 ' + duration(r.remaining_s) + '。证据有效至 ' + stamp(r.expires_s) + '。'
    : detail(r);
  show('remote-explain', remoteText);
  show('r-contact', r ? (contact[r.contact] || '未知') : '—');
  show('r-wet', r ? (wet[r.wet] || '未知') : '—');
  show('r-forecast', r ? (forecast[r.forecast] || '未知') : '—');
  show('r-conditions', r && r.conditions ? [].concat(r.conditions).join('；') : '—');
  show('sync-tag', !r ? '等待首次接收' : f.local_seq !== f.remote_seq ? '回放对照 · 远端版本滞后' : '回放对照 · 版本一致');
  byId('sync-tag').dataset.stale = String(!r || f.local_seq !== f.remote_seq);
  show('saved', stamp(f.saved_s)); show('received', stamp(f.received_s));
  show('link', f.link ? '可用' : '中断');
  show('queue', f.queue + ' 条'); show('drops', f.drops + ' 条');
  show('notices', f.newly_notified + ' 条');
  show('energy', f.energy_j.toFixed(2) + ' / ' + f.capacity_j.toFixed(0) + ' J');
  show('delivery', r && f.local_seq === f.remote_seq ? '最新现场状态已到模拟端' : '最新现场状态待同步');
  const journal = byId('events');
  journal.replaceChildren();
  for (const entry of current.frames.slice(0,index+1).filter(x => x.actions.length).slice(-5).reverse()) {
    const item = document.createElement('li');
    const when = document.createElement('time');
    when.textContent = stamp(entry.t);
    const text = document.createElement('span');
    text.textContent = [...new Set(entry.actions.map(a => actionNames[a] || a))].join(' · ');
    item.append(when, text); journal.append(item);
  }
}
function selectCase() {
  current = cases.find(c => c.id === picker.value);
  slider.max = current.frames.length - 1;
  const focus = {wet_connected:62, wet_reconnect:62, change_offline:73, boot_offline:73, ack_lost:68,
    manual_charge:14, no_charge:60, missing_packet:26, normal_cycle:26, forecast_boundary:60,
    forecast_wet:62, contact_missing:35, queue_full:35, model_mismatch:26, fault_revoke:35,
    probe_obstructed:35}[current.id];
  const i = current.frames.findIndex(f => f.t === focus);
  render(i < 0 ? 0 : i);
}
picker.addEventListener('change', selectCase);
slider.addEventListener('input', () => render(Number(slider.value)));
byId('prev').addEventListener('click', () => render(Number(slider.value)-1));
byId('next').addEventListener('click', () => render(Number(slider.value)+1));
picker.value = 'wet_reconnect';
selectCase();
