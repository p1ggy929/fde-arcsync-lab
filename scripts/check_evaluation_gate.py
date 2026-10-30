"""从受控数据库实时检查发布门禁，不信任可编辑的导出报告。"""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from arcsync.evaluation.repository import detail
from arcsync.evaluation.specs import suite
from arcsync.evaluation.profiles import metadata
from arcsync.admin.repository import prompt
from arcsync.data_models.config import ModelSettings


def validate(job,candidate_build,current_prompt,current_model,current_suite_hash):
    if job['gate']['status']!='ready_for_release_review':raise ValueError('评测门禁阻断：须完整执行30条并独立复核通过')
    snapshot=job['snapshot']
    if snapshot['code_version']!=candidate_build:raise ValueError('候选镜像与评测代码版本不一致，请重新评测')
    if snapshot['prompt']['content']!=current_prompt['content']:raise ValueError('系统提示词已变化，请重新评测')
    if snapshot['model']!=current_model:raise ValueError('当前模型配置与评测模型不一致，请重新评测')
    if snapshot['suite']['hash']!=current_suite_hash:raise ValueError('题库已变化，请重新评测')
    return True


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True)
    parser.add_argument('--candidate-build',required=True)
    args=parser.parse_args()
    try:
        validate(detail(args.job),args.candidate_build,prompt(),metadata(ModelSettings.load()),suite()['hash'])
        print('评测门禁通过；仍须完成生产安全检查与发布审批。')
    except Exception as error:
        print('发布未放行：'+(str(error) if isinstance(error,ValueError) else '无法读取或校验评测证据'),file=sys.stderr)
        sys.exit(1)
