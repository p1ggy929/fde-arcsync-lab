"""Workflow and authorization regression tests using isolated temporary databases."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from arcsync.core.store import init_db, connect, ROLES
from arcsync.core.service import Problem, business, mutate, state, readable_document

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'test.sqlite3')
        init_db(self.path)
        self.db=connect(self.path)
        self.org=self.user('organizer')
        self.rev=self.user('reviewer')
        self.bid='CY-2026-1023-001'
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def user(self,role): return {'role':role,'name':ROLES[role][0]}
    def act(self,user,action,**payload):
        if 'business_id' in payload and 'revision' not in payload:
            payload['revision']=business(self.db,payload['business_id'])['revision']
        return mutate(self.db,user,action,payload)
    def cite(self,**updates):
        data={'business_id':self.bid,'document_id':'YZ-CJ-1992-008','version':'v1.1','scan_page':6,'paper_page':46}
        data.update(updates)
        return self.act(self.org,'cite',**data)
    def test_restricted_content_not_in_state_or_direct_lookup(self):
        for role in ROLES:
            result=state(self.db,self.user(role))
            visible=any(d['level']=='受限' for d in result['documents'])
            self.assertEqual(visible,role=='protector')
        with self.assertRaises(Problem) as error: readable_document(self.db,self.org,'YZ-R-1991-002')
        self.assertEqual(error.exception.status,404)
        self.assertEqual(state(self.db,self.org)['audit'],[])
    def test_full_workflow_requires_acknowledged_handoffs(self):
        self.cite()
        self.act(self.org,'submit',business_id=self.bid,note='已核对第 46 页，交审核')
        b=business(self.db,self.bid)
        self.assertEqual(b['owner'],'organizer')
        with self.assertRaises(Problem): self.act(self.rev,'approve',business_id=self.bid,note='已审核')
        hid=self.db.execute("SELECT id FROM handoffs WHERE business_id=?",(self.bid,)).fetchone()[0]
        with self.assertRaises(Problem): self.act(self.org,'accept',id=hid)
        self.act(self.rev,'accept',id=hid)
        self.act(self.rev,'approve',business_id=self.bid,note='引用与适用范围核验通过')
        self.act(self.rev,'deliver',business_id=self.bid,note='已向申请单位人工交付公开资料')
        self.assertEqual(business(self.db,self.bid)['status'],'已交付')
        actions=[r[0] for r in self.db.execute('SELECT action FROM audit WHERE business_id=?',(self.bid,))]
        self.assertIn('人工确认卷页引用',actions)
        self.assertIn('确认接手',actions)
        self.assertIn('记录人工交付',actions)
    def test_new_application_authorization_waits_for_organizer(self):
        bid=self.act(self.user('representative'),'create',title='测试查阅',applicant='研究组',purpose='研究')['id']
        with self.assertRaises(Problem): self.act(self.org,'authorize',business_id=bid,scope='公开档案试点')
        self.act(self.rev,'authorize',business_id=bid,scope='公开档案试点')
        self.assertEqual(business(self.db,bid)['owner'],'reviewer')
        hid=self.db.execute('SELECT id FROM handoffs WHERE business_id=?',(bid,)).fetchone()[0]
        self.act(self.org,'accept',id=hid)
        self.assertEqual(business(self.db,bid)['owner'],'organizer')
    def test_stale_versions_bad_mapping_and_ambiguous_data_blocked(self):
        for data in [
          {'version':'v0'}, {'scan_page':0}, {'scan_page':19}, {'scan_page':True}, {'paper_page':6},
          {'document_id':'YZ-CJ-1987-009','version':'v1.0'},
          {'document_id':'YZ-CJ-1985-017','version':'v1.0'},
          {'document_id':'YZ-CJ-1986-001','version':'v2.0','paper_page':6},
        ]:
            with self.subTest(data=data),self.assertRaises(Problem): self.cite(**data)
        self.assertEqual(business(self.db,self.bid)['citations'],[])
    def test_correction_review_required_and_duplicate_citation_rejected(self):
        self.cite(document_id='YZ-CJ-1986-001',version='v2.0',paper_page=6,correction_checked=True)
        with self.assertRaises(Problem): self.cite(document_id='YZ-CJ-1986-001',version='v2.0',paper_page=6,correction_checked=True)
    def test_pause_denies_progress_but_allows_notes_and_manual_exceptions(self):
        self.act(self.user('protector'),'pause',paused=True,reason='专项测试发现问题')
        with self.assertRaises(Problem): self.cite()
        self.act(self.org,'note',business_id=self.bid,note='保留现场说明')
        self.act(self.org,'issue',business_id=self.bid,kind='缺少依据',detail='暂停期间人工复核')
        self.assertEqual(business(self.db,self.bid)['status'],'异常处理中')
        with self.assertRaises(Problem): self.act(self.user('director'),'pause',paused=False,reason='尝试恢复')
        self.act(self.user('protector'),'pause',paused=False,reason='问题已复盘')
    def test_conflicting_edits_do_not_overwrite_notes(self):
        revision=business(self.db,self.bid)['revision']
        self.act(self.org,'note',business_id=self.bid,note='先保存的内容')
        with self.assertRaises(Problem): self.act(self.org,'note',business_id=self.bid,revision=revision,note='陈旧内容')
        self.assertEqual(business(self.db,self.bid)['note'],'先保存的内容')
    def test_exception_assignment_resolution_and_source_unchanged(self):
        self.act(self.org,'issue',business_id=self.bid,document_id='YZ-CJ-1987-009',kind='页码歧义',detail='需核对')
        iid=self.db.execute('SELECT MAX(id) FROM issues').fetchone()[0]
        self.act(self.rev,'assign',business_id=self.bid,id=iid,receiver='engineer',note='请人工核对映射')
        with self.assertRaises(Problem): self.act(self.org,'resolve',id=iid,resolution='越岗处理')
        self.act(self.user('engineer'),'resolve',id=iid,resolution='已记录映射疑点，源资料待独立修复')
        self.assertEqual(business(self.db,self.bid)['status'],'待复核')
        self.assertEqual(readable_document(self.db,self.org,'YZ-CJ-1987-009')['status'],'待修复')
        hid=self.db.execute('SELECT MAX(id) FROM handoffs WHERE business_id=?',(self.bid,)).fetchone()[0]
        self.act(self.rev,'accept',id=hid)
        self.act(self.rev,'resume',business_id=self.bid,note='复核通过，重新定位有效资料')
        self.assertEqual(business(self.db,self.bid)['owner'],'reviewer')
        hid=self.db.execute('SELECT MAX(id) FROM handoffs WHERE business_id=?',(self.bid,)).fetchone()[0]
        self.act(self.org,'accept',id=hid)
        self.assertEqual(business(self.db,self.bid)['status'],'定位中')
    def test_return_reason_and_citations_cleared(self):
        bid='CY-2026-1023-002'
        with self.assertRaises(Problem): self.act(self.rev,'return',business_id=bid,reason='')
        self.act(self.rev,'return',business_id=bid,reason='补充适用范围')
        b=business(self.db,bid)
        self.assertEqual(b['return_reason'],'补充适用范围')
        self.assertEqual(b['citations'],[])
        self.assertEqual(b['owner'],'reviewer')
    def test_no_empty_approval_or_unreviewed_delivery(self):
        with self.assertRaises(Problem): self.act(self.org,'submit',business_id=self.bid,note='未核对')
        with self.assertRaises(Problem): self.act(self.rev,'deliver',business_id='CY-2026-1023-002',note='直接交付')
    def test_source_version_changes_rechecked_at_approval(self):
        self.db.execute("UPDATE documents SET version='v2.0' WHERE id='YZ-CJ-1992-008'")
        with self.assertRaises(Problem): self.act(self.rev,'approve',business_id='CY-2026-1023-002',note='过时引用')
    def test_audit_append_only(self):
        self.cite()
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute("UPDATE audit SET detail='{}'")
        with self.assertRaises(sqlite3.IntegrityError): self.db.execute('DELETE FROM audit')
    def test_database_persists_and_seed_is_idempotent(self):
        self.act(self.org,'note',business_id=self.bid,note='重启后保留')
        self.db.commit();init_db(self.path)
        with connect(self.path) as other:
            self.assertEqual(business(other,self.bid)['note'],'重启后保留')
            self.assertEqual(other.execute('SELECT COUNT(*) FROM documents').fetchone()[0],8)
    def test_feedback_separates_receipt_from_review(self):
        self.act(self.user('representative'),'feedback',business_id=self.bid,version='v2.0',content='需核对交接')
        fid=self.db.execute('SELECT MAX(id) FROM feedback').fetchone()[0]
        with self.assertRaises(Problem): self.act(self.user('representative'),'reply',id=fid,reply='自行裁定')
        self.act(self.rev,'reply',id=fid,reply='经核对尚未交付')
        self.assertEqual(self.db.execute('SELECT status FROM feedback WHERE id=?',(fid,)).fetchone()[0],'已核对')

    def test_restricted_metadata_not_leaked_through_audit_or_public_issues(self):
        from arcsync.core.store import audit
        audit(self.db,self.user('protector'),'查看合成卷页',detail={'document_id':'YZ-R-1991-002'})
        director=state(self.db,self.user('director'))
        self.assertNotIn('YZ-R-1991-002',json.dumps(director,ensure_ascii=False))
        with self.assertRaises(Problem):
            self.act(self.user('protector'),'issue',business_id=self.bid,document_id='YZ-R-1991-002',kind='权限核对',detail='不可传播')
        messages=[]
        for identifier in ('YZ-R-1991-002','DOES-NOT-EXIST'):
            with self.assertRaises(Problem) as caught:
                readable_document(self.db,self.org,identifier)
            messages.append((caught.exception.status,caught.exception.message))
        self.assertEqual(messages[0],messages[1])
    def test_duplicate_handoff_and_progress_before_acceptance_blocked(self):
        self.cite()
        self.act(self.org,'submit',business_id=self.bid,note='等待审核员接手')
        with self.assertRaises(Problem):
            self.act(self.org,'handoff',business_id=self.bid,receiver='reviewer',note='重复交接')
        with self.assertRaises(Problem):
            self.act(self.org,'cite',business_id=self.bid,document_id='YZ-CJ-1992-008',version='v1.1',scan_page=7,paper_page=47)

if __name__=='__main__': unittest.main()
