"""Execute the receipt UI's actual JavaScript with an isolated DOM/transport."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from app.api.web_admin import ADMIN_HTML


NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for UI execution")
SCRIPT = re.search(r"<script>(.*?)</script>", ADMIN_HTML, re.S).group(1)
SCRIPT = re.sub(r"\nboot\(\);\s*$", "\n", SCRIPT)

HARNESS = r"""
function element(tagName='INPUT',dataset={},value=''){
  return {tagName,dataset,value,disabled:false,hidden:false,textContent:'',innerHTML:'',className:'',
    classList:{toggle(){}},setAttribute(){},removeAttribute(){},focus(){}};
}
const nodes=new Map();
for(const id of ['view','side','raw','message','freshness','pageTitle','receiptError'])nodes.set(id,element('DIV'));
for(const id of ['receiptAmount','receiptPaidAt','receiptReference','receiptReason'])nodes.set(id,element());
const controlNodes=[];
const document={getElementById:id=>nodes.get(id)||null,querySelectorAll:()=>controlNodes};
const confirmCalls=[];
function confirm(message){confirmCalls.push(message);return true}
const location={href:''};
function fixtureReceipt(){
  const launch=element('BUTTON',{paymentId:'17',caseId:'4',paymentTitle:'Первый платёж',paymentAmount:'30000.00',expectedPaymentStatus:'PENDING',expectedPaymentUpdatedAt:'2026-10-20T10:00:00+00:00',expectedCaseStatus:'M1_WAITING_PAYMENT_30000',expectedCaseUpdatedAt:'2026-10-20T10:01:00+00:00'});
  controlNodes.push(launch);
  showOfflineReceiptForm(launch);
  const submit=element('BUTTON',{paymentId:'17',caseId:'4'});
  submit.textContent='Подтвердить поступление';
  nodes.set('receiptSubmit',submit);controlNodes.push(submit);
  nodes.get('receiptAmount').value='30000,00';
  nodes.get('receiptPaidAt').value='2026-10-21T09:30';
  nodes.get('receiptReference').value='Выписка № 123 / платёж 456';
  nodes.get('receiptReason').value='Сумма и назначение сверены с банковской выпиской';
  return submit;
}
const calls=[],refreshes=[];
function successfulTransport(){
  api=async(path,opts)=>{calls.push({path,payload:JSON.parse(opts.body)});return {ok:true,payment_id:17,status:'PAID',case_id:4,case_status:'M1_POWER_OF_ATTORNEY'}};
  openCase=async(id,propagate)=>{refreshes.push({kind:'case',id,propagate})};
  loadCurrent=async(propagate)=>{refreshes.push({kind:'ledger',propagate})};
}
"""


def run_ui(body: str):
    program = (
        "const vm=require('node:vm'); const assert=require('node:assert/strict');\n"
        "const context=vm.createContext({assert,AbortController,console});\n"
        f"vm.runInContext({json.dumps(HARNESS)},context);\n"
        f"vm.runInContext({json.dumps(SCRIPT)},context);\n"
        f"Promise.resolve(vm.runInContext({json.dumps('(async()=>{' + body + '})()')},context))"
        ".catch(error=>{console.error(error);process.exitCode=1});"
    )
    result = subprocess.run(
        [NODE, "-e", program], capture_output=True, text=True, timeout=15,
        env={**os.environ, "TZ": "Europe/Moscow"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_receipt_button_is_eligible_payment_specific_and_escapes_attributes():
    run_ui(r"""
const payment={id:17,case_id:4,offline_receipt_allowed:true,title:'"><img src=x onerror=alert(1)>',amount_exact:'30000.10',amount:30000.1,status:'PENDING',updated_at:'payment-version',case_status:'M1_WAITING_PAYMENT_30000',case_updated_at:'case-version'};
const html=offlineReceiptButton(payment);
assert(html.includes('data-payment-id="17"'));
assert(html.includes('data-case-id="4"'));
assert(html.includes('data-payment-amount="30000.10"'));
assert(html.includes('&quot;&gt;&lt;img'));
assert(!html.includes('<img'));
assert.equal(offlineReceiptButton({...payment,offline_receipt_allowed:false}),'');
assert.equal(offlineReceiptButton({...payment,id:'17 onclick=alert(1)'}),'');
fixtureReceipt();
assert(nodes.get('side').innerHTML.includes('id="offlineReceiptForm_17"'));
for(const id of ['receiptAmount','receiptPaidAt','receiptReference','receiptReason','receiptSubmit'])assert(nodes.get('side').innerHTML.includes('id="'+id+'"'));
assert(nodes.get('side').innerHTML.includes('фактически полученная оплата'));
assert(Object.isFrozen(offlineReceiptDraft));
""")


def test_case_card_and_ledger_expose_real_receipt_without_changing_test_payment():
    run_ui(r"""
const payment={id:17,case_id:4,title:'Первый платёж',amount_exact:'30000.00',amount:30000,status:'PENDING',updated_at:'payment-version',case_status:'M1_WAITING_PAYMENT_30000',case_updated_at:'case-version',offline_receipt_allowed:true,manual_confirm_allowed:false};
api=async path=>path==='/admin/payments'?[payment]:{case:{id:4,number:'TEST-4',status:'M1_WAITING_PAYMENT_30000',lawyer_id:3},payments:[payment],documents:[],court_events:[]};
await openCase(4);await loadPayments({signal:undefined});
for(const id of ['side','view']){
  assert(nodes.get(id).innerHTML.includes('data-action="record-offline-receipt"'));
  assert(!nodes.get(id).innerHTML.includes('Подтвердить тестовый платёж'));
}
payment.offline_receipt_allowed=false;payment.manual_confirm_allowed=true;
await openCase(4);await loadPayments({signal:undefined});
for(const id of ['side','view']){
  assert(!nodes.get(id).innerHTML.includes('data-action="record-offline-receipt"'));
  assert(nodes.get(id).innerHTML.includes('confirmPayment(17,4,this)'));
  assert(nodes.get(id).innerHTML.includes('Подтвердить тестовый платёж'));
}
payment.manual_confirm_allowed=false;payment.status='PAID';
payment.paid_at='2026-10-21T06:30:00Z';payment.confirmed_by=7;
payment.receipt_reference='Выписка <img src=x onerror=alert(1)>';
payment.confirmation_reason='<script>privateBankNote()</script>';
await openCase(4);await loadPayments({signal:undefined});
for(const id of ['side','view']){
  const html=nodes.get(id).innerHTML;
  assert(html.includes('Поступило'));assert(/21\.10\.(?:20)?26/.test(html));
  assert(html.includes('Подтвердил сотрудник #7'));
  assert(html.includes('Выписка &lt;img'));assert(!html.includes('<img'));
  assert(html.includes('&lt;script&gt;privateBankNote()'));assert(!html.includes('<script>'));
  assert(!html.includes('data-action="record-offline-receipt"'));
  assert(!html.includes('Подтвердить тестовый платёж'));
}
""")


@pytest.mark.parametrize(("field", "value"), [
    ("receiptAmount", "0"), ("receiptAmount", "-30000"),
    ("receiptAmount", "30000.123"), ("receiptAmount", "not-a-number"),
    ("receiptPaidAt", ""), ("receiptPaidAt", "not-a-date"),
    ("receiptReference", " "), ("receiptReference", "x" * 256),
    ("receiptReason", "four"), ("receiptReason", "x" * 2001),
])
def test_invalid_receipt_does_not_write_and_preserves_entered_values(field, value):
    run_ui(
        "const button=fixtureReceipt();successfulTransport();"
        f"nodes.get({json.dumps(field)}).value={json.dumps(value)};"
        "await recordOfflineReceipt(button);"
        "assert.equal(calls.length,0);assert.equal(confirmCalls.length,0);"
        "assert.equal(nodes.get('receiptError').hidden,false);"
        "assert(offlineReceiptDraft);"
        f"assert.equal(nodes.get({json.dumps(field)}).value,{json.dumps(value)});"
    )


def test_confirmed_receipt_sends_decimal_timezone_and_both_snapshots():
    run_ui(r"""
const button=fixtureReceipt();successfulTransport();
await recordOfflineReceipt(button);
assert.equal(calls.length,1);
assert.equal(calls[0].path,'/admin/payments/17/record-receipt');
assert.deepEqual(calls[0].payload,{amount:'30000.00',paid_at:'2026-10-21T06:30:00.000Z',reference:'Выписка № 123 / платёж 456',reason:'Сумма и назначение сверены с банковской выпиской',case_id:4,expected_payment_status:'PENDING',expected_payment_updated_at:'2026-10-20T10:00:00+00:00',expected_case_status:'M1_WAITING_PAYMENT_30000',expected_case_updated_at:'2026-10-20T10:01:00+00:00'});
assert.equal(confirmCalls.length,1);
assert(confirmCalls[0].includes('30000.00 ₽'));
assert.equal(offlineReceiptDraft,null);
assert(recordedOfflinePayments.has(17));
assert.equal(nodes.get('message').className,'ok');
assert(nodes.get('message').textContent.includes('зафиксировано'));
assert.equal(refreshes.length,2);
assert(refreshes.every(item=>item.propagate===true));
assert.equal(pending.size,0);
assert.equal(button.disabled,false);
""")


def test_cancelled_confirmation_does_not_write_or_discard_the_form():
    run_ui(r"""
const button=fixtureReceipt();successfulTransport();confirm=()=>false;
await recordOfflineReceipt(button);
assert.equal(calls.length,0);assert.equal(refreshes.length,0);
assert(offlineReceiptDraft);assert.equal(recordedOfflinePayments.size,0);
assert.equal(nodes.get('receiptReference').value,'Выписка № 123 / платёж 456');
""")


def test_in_flight_receipt_locks_case_and_payment_and_suppresses_double_submit():
    run_ui(r"""
const button=fixtureReceipt();successfulTransport();let finish;
api=(path,opts)=>{calls.push({path,payload:JSON.parse(opts.body)});return new Promise(resolve=>{finish=resolve})};
const first=recordOfflineReceipt(button);
assert(pending.has('payment:17'));assert(pending.has('case:4'));
assert.equal(button.disabled,true);
await recordOfflineReceipt(button);
assert.equal(calls.length,1);
finish({ok:true,payment_id:17,status:'PAID',case_id:4,case_status:'M1_POWER_OF_ATTORNEY'});
await first;
assert.equal(calls.length,1);assert.equal(pending.size,0);
assert.equal(button.disabled,false);
""")


def test_failed_write_retains_draft_and_reports_no_success():
    run_ui(r"""
const button=fixtureReceipt();successfulTransport();
api=async()=>{throw new Error('Статус платежа изменился. Обновите экран')};
await recordOfflineReceipt(button);
assert(offlineReceiptDraft);assert.equal(recordedOfflinePayments.size,0);
assert.equal(refreshes.length,0);assert.equal(pending.size,0);
assert.equal(nodes.get('message').className,'bad');
assert(nodes.get('receiptError').textContent.includes('Статус платежа изменился'));
assert.equal(nodes.get('receiptReason').value,'Сумма и назначение сверены с банковской выпиской');
""")


def test_successful_write_with_failed_refresh_remains_a_saved_result():
    run_ui(r"""
const button=fixtureReceipt();successfulTransport();
loadCurrent=async()=>{feedback('');throw new Error('Список временно недоступен')};
await recordOfflineReceipt(button);
assert.equal(calls.length,1);assert.equal(offlineReceiptDraft,null);
assert.equal(nodes.get('message').className,'warn-text');
assert(nodes.get('message').textContent.includes('зафиксировано'));
assert(nodes.get('message').textContent.includes('Экран не обновился'));
assert(recordedOfflinePayments.has(17));
showOfflineReceiptForm(controlNodes[0]);
assert.equal(offlineReceiptDraft,null);
assert.equal(calls.length,1);
""")


def test_refresh_helpers_can_propagate_errors_without_changing_default_behavior():
    run_ui(r"""
api=async()=>{throw new Error('Read unavailable')};
await assert.rejects(()=>openCase(4,true),/Read unavailable/);
await assert.rejects(()=>loadCurrent(true),/Read unavailable/);
await openCase(4);await loadCurrent();
assert(nodes.get('view').innerHTML.includes('Read unavailable'));
""")
