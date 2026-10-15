const $ = id => document.getElementById(id);
const shield = new PaymentShield();
let config, order, merchantToken = '';
const money = amount => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:0}).format(amount / 100);
const percent = value => `${(value * 100).toFixed(1)}%`;
const human = value => value.replaceAll('_', ' ');
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function message(text) { $('global-message').hidden = !text; $('global-message').textContent = text || ''; }
async function action(button, callback) {
  button.disabled = true; message('');
  try { await callback(); } catch(error) { message(error.message); }
  finally { button.disabled = false; }
}
function showPage(name) {
  document.querySelectorAll('.page').forEach(page => page.hidden = page.id !== `page-${name}`);
  document.querySelectorAll('.nav').forEach(button => button.classList.toggle('active',button.dataset.page === name));
  $('page-label').textContent = document.querySelector(`[data-page="${name}"]`).textContent.trim().replace(/^[^A-Za-z]+/,'');
  message('');
  if (name === 'model') loadModel();
}
document.querySelectorAll('.nav').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
function updateProduct() {
  const product = config.catalog[$('item').value];
  $('price').textContent = money(product.amount_paise * Number($('quantity').value));
  $('product-category').textContent = product.category;
}
function clearOrder() { order = undefined; sessionStorage.removeItem('ps_order'); $('decision').hidden = true; $('decision-empty').hidden = false; $('demo-bank').hidden = true; }
$('item').addEventListener('change', () => { updateProduct(); clearOrder(); });
$('quantity').addEventListener('input', () => { updateProduct(); clearOrder(); });
$('scenario').addEventListener('change', clearOrder);
function renderOrder(value) {
  order = value; sessionStorage.setItem('ps_order', value.id);
  $('decision-empty').hidden = true; $('decision').hidden = false;
  const assessment = value.assessment;
  $('risk-score').textContent = assessment.score.toFixed(1);
  $('risk-fill').style.left = `${Math.min(99,assessment.score)}%`;
  const badge = $('decision-badge'); badge.textContent = {proceed:'Proceed',review:'Review required',stop:'Stopped'}[assessment.decision]; badge.className = `decision-badge ${assessment.decision}`;
  $('score-note').textContent = assessment.note;
  $('reasons').replaceChildren(...assessment.reasons.map(reason => element('li',reason)));
  $('order-state').textContent = human(value.state); $('order-id').textContent = value.id;
  $('pay').hidden = !['ready','provider_pending'].includes(value.state);
  $('pay').textContent = config.demo ? 'Continue to demo payment →' : 'Open provider test checkout →';
  $('demo-bank').hidden = true;
  if (value.events) $('timeline').replaceChildren(...value.events.map(event => {
    const item = element('li',human(event.kind)); item.append(element('small',`${new Date(event.created*1000).toLocaleTimeString()} · ${event.detail}`)); return item;
  }));
}
async function refresh() { if (order) renderOrder(await shield.getOrder(order.id)); }
$('assess').addEventListener('click', () => action($('assess'), async () => {
  if (!$('quantity').reportValidity()) return;
  const created = await shield.createOrder({item:$('item').value,quantity:Number($('quantity').value),scenario:$('scenario').value,idempotency_key:crypto.randomUUID()});
  renderOrder(created); await refresh();
}));
$('pay').addEventListener('click', () => action($('pay'), async () => {
  const result = await shield.checkout(order.id);
  await refresh();
  if (result.demo) { $('pay').hidden = true; $('demo-bank').hidden = false; }
  if (result.cancelled) message('Checkout closed. No successful payment has been confirmed.');
}));
for (const outcome of ['success','failure']) $(`demo-${outcome}`).addEventListener('click', () => action($(`demo-${outcome}`), async () => {
  await shield.request(`/api/v1/orders/${order.id}/demo-complete?outcome=${outcome}`, {}); await refresh();
}));
$('refresh-order').addEventListener('click', () => action($('refresh-order'),refresh));

const svgNS = 'http://www.w3.org/2000/svg';
function svg(tag, attrs, text) { const node = document.createElementNS(svgNS,tag); for (const [key,value] of Object.entries(attrs)) node.setAttribute(key,value); if (text !== undefined) node.textContent=text; return node; }
function chart(xs, ys, xLabel, yLabel, diagonal=false, baseline) {
  const root=svg('svg',{viewBox:'0 0 380 260',class:'chart',role:'img','aria-label':`${yLabel} versus ${xLabel}`});
  for (let i=0;i<=4;i++) { const fraction=i/4; root.append(svg('line',{x1:42,y1:215-fraction*180,x2:355,y2:215-fraction*180}),svg('text',{x:32,y:219-fraction*180,'text-anchor':'end'},fraction.toFixed(2)),svg('text',{x:42+fraction*313,y:232,'text-anchor':'middle'},fraction.toFixed(2))); }
  if(diagonal) root.append(svg('path',{d:'M42,215 L355,35',style:'stroke:#bcc9bd;stroke-dasharray:5 5;stroke-width:1.5'}));
  if(baseline !== undefined) root.append(svg('line',{x1:42,x2:355,y1:215-baseline*180,y2:215-baseline*180,style:'stroke:#b69b67;stroke-dasharray:5 5'}));
  root.append(svg('path',{d:xs.map((x,i)=>`${i?'L':'M'}${42+x*313},${215-ys[i]*180}`).join(' ')}),svg('text',{x:200,y:254,'text-anchor':'middle'},xLabel),svg('text',{x:42,y:16},yLabel)); return root;
}
function matrix(result) {
  const root=element('div',undefined,'matrix');
  ['', 'Predicted proceed','Predicted flag'].forEach(text=>root.append(element('div',text)));
  result.confusion_matrix.forEach((row,index)=>{
    root.append(element('div',index?'Actual suspicious':'Actual legitimate'));
    row.forEach((value,col)=>{const cell=element('div',String(value),`cell ${index===col?'good':'error'}`);cell.append(element('small',[['True negatives','False alarms'],['Missed cases','True positives']][index][col]));root.append(cell);});
  }); return root;
}
function chartCard(title, child, caption) { const card=element('article',undefined,'card'); card.append(element('h2',title),child,element('p',caption,'caption')); return card; }
function table(headers, rows) { const wrap=element('div',undefined,'table-wrap'), t=element('table'), head=element('thead'), tr=element('tr'), body=element('tbody'); headers.forEach(text=>tr.append(element('th',text)));head.append(tr);rows.forEach(row=>{const tr=element('tr');row.forEach(text=>tr.append(element('td',String(text))));body.append(tr);});t.append(head,body);wrap.append(t);return wrap; }
async function loadModel() {
  try {
    const report=await shield.request('/api/v1/evaluation'), result=report.test, root=$('model-content');root.replaceChildren();
    const title=element('div',undefined,'model-title');title.append(element('h2',human(report.selected_model)),element('span',`SYNTHETIC · ${result.rows.toLocaleString()} TEST ROWS`,'pill'));root.append(title);
    const stats=element('div',undefined,'stats');
    [['Precision',percent(result.precision),'Among model-flagged examples'],['Recall',percent(result.recall),'Suspicious examples found'],['False-positive rate',percent(result.false_positive_rate),'Legitimate examples flagged'],['Average precision',result.average_precision.toFixed(3),'PR ranking, not accuracy']].forEach(([name,value,note])=>{const card=element('div',undefined,'stat');card.append(element('label',name),element('strong',value),element('small',note));stats.append(card);});root.append(stats);
    root.append(element('p',`Review threshold: ${report.thresholds.review.toFixed(2)} · Model-only stop: ${report.thresholds.block>1?'disabled — insufficient validation evidence':report.thresholds.block.toFixed(2)} · ${result.false_alarms} false alarms · ${result.missed_cases} missed suspicious cases`,'caption'));
    const charts=element('div',undefined,'charts');charts.append(chartCard('Confusion matrix · model only',matrix(result),'Rows = synthetic true labels. Columns = the model’s decision at the validation-selected review threshold.'),chartCard('Precision–recall',chart(result.pr_curve.recall,result.pr_curve.precision,'Recall','Precision',false,result.positive_rows/result.rows),'Dashed line = synthetic positive-label prevalence. Higher recall can come with more false alarms.'),chartCard('ROC curve',chart(result.roc_curve.fpr,result.roc_curve.tpr,'False-positive rate','True-positive rate',true),`ROC-AUC ${result.roc_auc.toFixed(3)}. Always inspect precision–recall alongside this curve.`),chartCard('Calibration check',chart(result.calibration.predicted,result.calibration.observed,'Mean predicted score','Observed synthetic positive share',true),`Brier score ${result.brier_score.toFixed(4)} (lower is better). Calibration here applies only to this simulator.`));root.append(charts);
    const comparison=element('article',undefined,'card');comparison.append(element('h2','Model comparison · validation only'),table(['Candidate','Average precision','Precision','Recall','False-positive rate'],Object.entries(report.validation_comparison).map(([name,metric])=>[human(name),metric.average_precision.toFixed(3),percent(metric.precision),percent(metric.recall),percent(metric.false_positive_rate)])),element('p',report.selection,'caption'));root.append(comparison);
    const policies=element('article',undefined,'card');policies.style.marginTop='20px';policies.append(element('h2','What the complete policy changes'),table(['Test policy','False alarms','Missed cases','Precision','Recall'],[['Model only',result],['Rules only',report.baselines.rules_only],['Combined model + rules',report.combined_policy_test],['Always legitimate baseline',report.baselines.always_legitimate]].map(([name,metric])=>[name,metric.false_alarms,metric.missed_cases,percent(metric.precision),percent(metric.recall)])),element('p','The combined row evaluates proceed versus review/stop, not confirmed fraud or final merchant outcomes. Missing models and anonymous users take a separate fail-closed review path.','caption'));root.append(policies);
    const splits=element('article',undefined,'card');splits.style.marginTop='20px';splits.append(element('h2','Holdout and leakage audit'),table(['Split','Rows','Distinct users','Positive labels'],Object.entries(report.data).map(([name,part])=>[name,part.rows,part.users,part.positives])),element('p','Disjoint users and chronological windows. Identifiers, outcomes, and split labels are excluded from the feature vector. Demo scenarios are not training examples.','caption'));root.append(splits);
    const limitations=element('div',undefined,'bottom-note');limitations.append(element('strong','Read this before presenting the numbers.'));report.limitations.forEach(text=>limitations.append(element('p',text)));root.append(limitations);
  } catch(error) { $('model-content').replaceChildren(element('p',`${error.message} Run python scripts/train_checkout.py, then restart the connector.`,'notice')); }
}
$('load-orders').addEventListener('click', () => action($('load-orders'), async () => {
  merchantToken=$('merchant-token').value; await loadMerchant();
}));
async function loadMerchant() {
  const orders=await shield.request('/api/v1/merchant/orders',undefined,{headers:{Authorization:`Bearer ${merchantToken}`}}),root=$('merchant-orders');root.replaceChildren();
  if (!orders.length) root.append(element('p','No orders yet. Create one in the checkout playground.','notice'));
  for (const item of orders) {
    const card=element('article',undefined,'card review-item'),heading=element('div',undefined,'card-heading');heading.append(element('h2',`${money(item.amount)} · ${human(item.item)}`),element('span',human(item.state),'pill'));card.append(heading,element('p',item.id,'micro mono'));
    const list=element('ul',undefined,'reasons');item.assessment.reasons.forEach(reason=>list.append(element('li',reason)));card.append(list);
    if(item.state==='review') {
      const note=element('input');note.placeholder='Describe the independent verification performed';note.setAttribute('aria-label',`Review note for ${item.id}`);note.maxLength=500;const buttons=element('div',undefined,'actions');
      for(const approve of [true,false]) {const button=element('button',approve?'Clear for checkout':'Decline',approve?'primary':'secondary');button.addEventListener('click',()=>action(button,async()=>{await shield.request(`/api/v1/merchant/orders/${item.id}/review`,{approve,note:note.value},{headers:{Authorization:`Bearer ${merchantToken}`}});await loadMerchant();}));buttons.append(button);}card.append(note,buttons);
    }root.append(card);
  }
}
async function init() {
  try {
    config=await shield.request('/api/v1/config');$('mode').textContent=config.demo?'DEMO MODE · NO MONEY MOVES':'RAZORPAY TEST MODE';$('provider-notice').textContent=config.notice;
    $('scenario-wrap').hidden=!config.demo;
    for(const [id,item] of Object.entries(config.catalog)){const option=element('option',item.name);option.value=id;$('item').append(option);}updateProduct();
    const saved=sessionStorage.getItem('ps_order');if(saved)try{renderOrder(await shield.getOrder(saved));}catch{sessionStorage.removeItem('ps_order');}
  } catch(error) {message(error.message);$('assess').disabled=true;}
}
init();
