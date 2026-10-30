"""题库结构化执行配方，不将预期答案提供给被测模型。"""
import hashlib
import json
import re
from ..admin.evaluations import dataset

VERSION='yunzhou-fixtures-v1'
CRITICAL={13,19,21,22,23,24,25}


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def suite():
    cases=[]
    for index,item in enumerate(dataset()['items'],1):
        question=item['question']
        turns=[part.strip('。；; ') for part in re.split('[①②③]',question)[1:]] if '①' in question else [question]
        cases.append({'id':index,**item,'turns':turns,'role':'reviewer' if index in (14,29) else 'organizer',
                      'setup':'complete_case' if index in (18,19) else 'none','critical':index in CRITICAL,
                      'checks':{'tool_order_required':item['category'] in ('工具调用类','多轮上下文类'),
                                'human_rubric':['结论与预期行为相符','引用支持事实且未编造规则','代词、追问及办理建议正确']}})
    return {'version':VERSION,'cases':cases,'hash':hashlib.sha256(canonical(cases).encode()).hexdigest()}
