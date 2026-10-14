const byId = id => document.getElementById(id);
let inputMode = 'details', stream;
const node = (tag, text, cls) => { const el = document.createElement(tag); if (text !== undefined) el.textContent=text; if(cls)el.className=cls; return el; };
function notify(text) { byId('notification').textContent=text||'';byId('notification').hidden=!text; }
async function api(path, payload, headers={}) {
  const response=await fetch(path,{method:payload===undefined?'GET':'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',...headers},...(payload===undefined?{}:{body:JSON.stringify(payload)})});
  let result;try{result=await response.json();}catch{throw new Error('The service returned an unexpected response.');}
  if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Check the payment fields and try again.');return result;
}
async function busy(button, callback) { button.disabled=true;notify('');try{await callback();}catch(error){notify(error.message);}finally{button.disabled=false;} }
function money(value){if(!/^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,2})?$/.test(value.trim()))throw new Error('Enter an INR amount with up to two decimal places.');const [whole,fraction='']=value.trim().split('.');const amount=Number(whole)*100+Number(fraction.padEnd(2,'0'));if(amount<1||amount>100000000)throw new Error('Supported amounts are ₹0.01 to ₹10,00,000.');return amount;}
document.querySelectorAll('[data-panel]').forEach(button=>button.addEventListener('click',()=>{
  document.querySelectorAll('.panel').forEach(panel=>panel.hidden=panel.id!==`panel-${button.dataset.panel}`);
  document.querySelectorAll('[data-panel]').forEach(other=>{const active=other===button;other.classList.toggle('active',active);other.setAttribute('aria-pressed',String(active));});notify('');
}));
document.querySelectorAll('[data-input]').forEach(button=>button.addEventListener('click',()=>{
  inputMode=button.dataset.input;document.querySelectorAll('[data-input]').forEach(other=>{const active=other===button;other.classList.toggle('selected',active);other.setAttribute('aria-pressed',String(active));});
  for(const mode of ['details','uri','image'])byId(`input-${mode}`).hidden=mode!==inputMode;
  byId('result').hidden=true;byId('result-empty').hidden=false;
}));
function renderResult(result, root) {
  root.replaceChildren();root.hidden=false;
  const heading=node('div',undefined,'decision-heading');heading.append(node('div',{stop:'Stop this payment',verify:'Verify before paying',proceed:'No configured warning'}[result.decision],`decision-label ${result.decision}`));
  const score=node('div',undefined,'index-box');score.append(node('strong',`${result.risk_index}/100`),node('small','WARNING INDEX · NOT A PROBABILITY'));heading.append(score);root.append(heading,node('p',result.recommendation,'recommendation'));
  if(result.signals.length)result.signals.forEach(signal=>{const card=node('div',undefined,`signal ${signal.severity}`);card.append(node('strong',signal.title),node('p',signal.detail));root.append(card);});
  else root.append(node('p','No configured warning rule was triggered. Missing evidence can still prevent clearance.','micro'));
  if(result.qr){root.append(node('div','Decoded QR details','result-section'),node('p',`${result.qr.payee} · ${result.qr.encoded_amount_paise===null?'No encoded amount':new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR'}).format(result.qr.encoded_amount_paise/100)}`));if(result.qr.self_declared_name)root.append(node('p',`Self-declared name: ${result.qr.self_declared_name}. Not bank-verified.`,'micro'));}
  if(result.missing_evidence.length){root.append(node('div','What we cannot establish','result-section'));const list=node('ul',undefined,'evidence-list');result.missing_evidence.forEach(text=>list.append(node('li',text)));root.append(list);}
  if(result.environment){root.append(node('div','Recorded integration evidence','result-section'));const e=result.evidence;root.append(node('p',`${e.previous_attempts_10m} earlier attempts / 10 min · ${e.failed_outcomes_1h} failures / hour · ${e.paid_history_count} prior paid outcomes`,'micro'));}
  root.append(node('div',`${result.disclaimer} ${result.environment?`Environment: ${result.environment.toUpperCase()}. Outcomes are integration-reported.`:'User-supplied check. No app history or execution permit is available.'}`,'provenance'));
  if(result.independent_review_reported)root.append(node('p','The integrating app reports independent verification. That is not bank identity verification.','micro'));
}
function fileBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('Could not read the image.'));reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file);});}
byId('check-form').addEventListener('submit',event=>{event.preventDefault();busy(byId('check-button'),async()=>{
  const common={amount_paise:money(byId('amount').value),message:byId('message').value,purpose:byId('purpose').value};
  if(byId('expected-payee').value.trim())common.expected_payee=byId('expected-payee').value.trim();
  let result;
  if(inputMode==='details'){const payee=byId('payee').value.trim();if(!payee)throw new Error('Enter the recipient UPI ID.');result=await api('/api/v2/check',{...common,payee});}
  else if(inputMode==='uri'){result=await api('/api/v2/check-qr',{...common,payload:byId('upi-uri').value.trim()});}
  else{const file=byId('qr-image').files[0];if(!file)throw new Error('Choose a QR image.');if(file.size>8*1024*1024)throw new Error('Choose an image smaller than 8 MiB.');result=await api('/api/v2/check-qr-image',{...common,image_base64:await fileBase64(file)});}
  byId('result-empty').hidden=true;renderResult(result,byId('result'));
});});
function renderFeed(state){
  byId('stream-status').textContent='STREAM CONNECTED';byId('feed-summary').hidden=false;
  byId('feed-summary').replaceChildren(node('span',`${state.counts.live||0} app-labelled LIVE requests`),node('span',`${state.counts.test||0} TEST requests`),node('span',state.last_request_at?`Last event ${new Date(state.last_request_at*1000).toLocaleTimeString()}`:'No requests received'));
  const feed=byId('feed');feed.replaceChildren();
  if(!state.assessments.length){feed.append(node('p','Connected to this service. No app has submitted an assessment yet.','micro'));return;}
  state.assessments.forEach(result=>{const item=node('div',undefined,'feed-item');const info=node('div');const button=node('button',result.assessment_id);button.addEventListener('click',()=>{renderResult(result,byId('feed-detail'));byId('feed-detail').scrollIntoView({behavior:'smooth',block:'nearest'});});info.append(button,node('small',`${result.environment.toUpperCase()} · ${new Date(result.received_at*1000).toLocaleTimeString()} · ${result.latency_ms??'—'} ms initial assessment · ${result.payment_status||'No outcome reported'}`));item.append(info,node('span',result.decision.toUpperCase(),`tag ${result.decision}`));feed.append(item);});
}
async function startStream(){
  if(stream)stream.close();renderFeed(await api('/api/v2/console/state'));
  stream=new EventSource('/api/v2/console/stream');
  stream.addEventListener('state',event=>{try{renderFeed(JSON.parse(event.data));}catch{notify('Could not read a stream update.');}});
  stream.addEventListener('expired',()=>{stream.close();byId('stream-status').textContent='SESSION EXPIRED';notify('Operator session expired. Connect again.');});
  stream.onerror=()=>{byId('stream-status').textContent='RECONNECTING';};
}
byId('connect-console').addEventListener('click',()=>busy(byId('connect-console'),async()=>{
  const token=byId('operator-token').value;await api('/api/v2/console/session',{}, {Authorization:`Bearer ${token}`});byId('operator-token').value='';await startStream();
}));
function confusionMatrix(matrix){const root=node('div',undefined,'matrix');['','Predicted legitimate','Predicted suspicious'].forEach(text=>root.append(node('div',text)));matrix.forEach((row,i)=>{root.append(node('div',i?'Label: suspicious':'Label: legitimate'));row.forEach((value,j)=>root.append(node('div',String(value),`cell ${i===j?'':'error'}`)));});return root;}
byId('load-model').addEventListener('click',()=>busy(byId('load-model'),async()=>{
  const state=await api('/api/v2/console/state'),data=await api('/api/v2/console/model'),root=byId('model-report');root.replaceChildren();
  root.append(node('div',`${state.training_data.labelled_live_events} labelled live events · ${state.training_data.positive_labels} positive labels. ${state.training_data.provenance}`,'audit'));
  if(!data.available){root.append(node('h3','No observed-data evaluation yet.'),node('p',data.reason));return;}
  const report=data.report,result=report.test;root.append(node('h3',report.selected_model.replaceAll('_',' ')),node('p',`${result.rows} temporal holdout rows. Experimental report only; live ML enforcement stays disabled.`));
  const stats=node('div',undefined,'mini-stats');[['Precision',result.precision],['Recall',result.recall],['False-positive rate',result.false_positive_rate]].forEach(([name,value])=>{const part=node('div',name);part.append(node('strong',`${(value*100).toFixed(1)}%`));stats.append(part);});root.append(stats,node('h3','Observed-label confusion matrix'),confusionMatrix(result.confusion_matrix),node('p',`${result.false_alarms} false alarms; ${result.missed_cases} missed labelled cases. Labels are supplied by the integrator, not independently verified.`));
}));
window.addEventListener('pagehide',()=>{if(stream)stream.close();});
api('/api/v2/status').then(config=>{byId('service-status').textContent='Risk API online · No bank feed';byId('integration-status').textContent=config.integration_configured?'APP KEY CONFIGURED':'APP KEY NOT CONFIGURED';byId('version').textContent=config.engine_version;}).catch(()=>{byId('service-status').textContent='Risk API unavailable';notify('Start the live service to check payments.');});
