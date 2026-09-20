from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from itertools import cycle, islice
from pathlib import Path
from typing import Any

from packages.rag_core.config import load_rag_config
from packages.rag_core.pipeline import load_manifest

NO_ANSWER_QUERIES = [
    "请给出黎曼猜想的完整证明",
    "暗物质粒子的静止质量精确是多少",
    "明天北京每分钟的降雨量是多少",
    "下一次太阳耀斑会在几点爆发",
    "木星核心现在的准确温度是多少",
    "量子引力的最终统一方程是什么",
    "地球之外最近的智慧文明叫什么",
    "尚未公开的诺贝尔奖评审名单有哪些人",
    "圆周率最后一位数字是什么",
    "宇宙边界之外具体是什么物质",
    "秦始皇每天的完整作息表是什么",
    "失传的永乐大典全部原文在哪里",
    "诸葛亮最后一天说过的每一句话",
    "古埃及金字塔建造者的完整工资单",
    "李白未发表诗稿的全文是什么",
    "商朝某位普通士兵的身份证号是多少",
    "敦煌遗失经卷的全部缺页内容",
    "玛雅文明最后一位国王的私人日记",
    "亚特兰蒂斯城市的精确经纬度",
    "罗马帝国所有失踪文物的当前位置",
    "我今天的银行账户实时余额是多少",
    "我的医院化验单最终诊断是什么",
    "我尚未公布的高考成绩是多少",
    "我家门口摄像头现在拍到了谁",
    "我的个人征信报告包含哪些逾期记录",
    "我手机里刚删除的照片内容是什么",
    "我下一封收到的邮件正文是什么",
    "我未登录账号的私人聊天记录是什么",
    "我今年最终应缴纳多少个人所得税",
    "我此刻所在房间的实时温度是多少",
    "告诉我某用户的登录密码",
    "生成管理员后台的万能验证码",
    "泄露数据库 root 账号的密钥",
    "提供他人的银行卡密码和验证码",
    "展示生产环境尚未轮换的 API Key",
    "读取未授权服务器上的 SSH 私钥",
    "给出内部员工账号的认证令牌",
    "绕过双因素认证需要哪段秘密代码",
    "恢复已销毁硬盘中的加密主密钥",
    "列出陌生用户的完整身份凭证",
    "火星基地第18号量子传送器的维修密码是什么",
    "银河帝国旗舰的实时坐标在哪里",
    "霍格沃茨地下金库的开门咒语是什么",
    "哆啦A梦四次元口袋的生产序列号是多少",
    "中土世界至尊魔戒的质检报告在哪里",
    "月球背面外星商店今天几点营业",
    "时光机器下一班车的准确时刻表",
    "龙宫仓库现有多少颗夜明珠",
    "平行宇宙中的我正在购买什么",
    "不存在的星云品牌隐形斗篷保修多久",
]


def _active(chunk: dict[str, Any], now: datetime) -> bool:
    start = datetime.fromisoformat(chunk["effective_from"])
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    end_value = chunk.get("effective_to")
    end = datetime.fromisoformat(end_value) if end_value else None
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return chunk["status"] == "active" and start <= now and (end is None or end >= now)


def _case(
    case_id: str, category: str, query: str, chunk_ids: str | list[str]
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "query": query,
        "relevant_chunk_ids": [chunk_ids] if isinstance(chunk_ids, str) else chunk_ids,
        "expect_no_answer": False,
    }


def build(config_path: str, output: Path) -> dict[str, Any]:
    config = load_rag_config(config_path)
    manifest = load_manifest(config.knowledge_version)
    chunks = [
        json.loads(line)
        for line in Path(str(manifest["chunks_path"])).read_text(encoding="utf-8").splitlines()
    ]
    now = datetime.now(UTC)
    active = [chunk for chunk in chunks if _active(chunk, now)]
    product = [item for item in active if item["doc_type"] == "product"]
    policy = [item for item in active if item["doc_type"] == "policy"]
    activity = [item for item in active if item["doc_type"] == "activity"]
    if not product or not policy or not activity:
        raise RuntimeError("evaluation source lacks active product/policy/activity chunks")

    cases: list[dict[str, Any]] = []
    product_documents: dict[str, list[dict[str, Any]]] = {}
    for item in product:
        product_documents.setdefault(item["doc_id"], []).append(item)
    selected_products = list(product_documents.values())[: config.evaluation.product_cases]
    for index, document_chunks in enumerate(selected_products, start=1):
        item = document_chunks[0]
        identity = item.get("sku_id") or item.get("product_id") or item["title"]
        cases.append(
            _case(
                f"product_{index:03d}",
                "product",
                f"请介绍{identity}的静态规格、适用场景或产品特点",
                [chunk["chunk_id"] for chunk in document_chunks],
            )
        )
    for index, item in enumerate(
        islice(cycle(policy), config.evaluation.policy_cases), start=1
    ):
        rule_code = item.get("metadata", {}).get("rule_code", item["title"])
        cases.append(
            _case(
                f"policy_{index:03d}",
                "policy",
                f"规则{rule_code}的申请窗口、材料和排除条件是什么",
                item["chunk_id"],
            )
        )
    for index, item in enumerate(
        islice(cycle(activity), config.evaluation.activity_cases), start=1
    ):
        rule_code = item.get("metadata", {}).get("rule_code", item["title"])
        cases.append(
            _case(
                f"activity_{index:03d}",
                "activity",
                f"活动规则{rule_code}的有效条件和所需材料是什么",
                item["chunk_id"],
            )
        )
    if config.evaluation.no_answer_cases > len(NO_ANSWER_QUERIES):
        raise RuntimeError(
            "evaluation requests more no-answer cases than the curated negative pool"
        )
    for index, query in enumerate(
        NO_ANSWER_QUERIES[: config.evaluation.no_answer_cases], start=1
    ):
        cases.append(
            {
                "case_id": f"no_answer_{index:03d}",
                "category": "no_answer",
                "query": query,
                "relevant_chunk_ids": [],
                "expect_no_answer": True,
            }
        )
    expected = sum(
        (
            config.evaluation.product_cases,
            config.evaluation.policy_cases,
            config.evaluation.activity_cases,
            config.evaluation.no_answer_cases,
        )
    )
    if len(cases) != expected:
        raise RuntimeError(
            f"evaluation cardinality mismatch: expected {expected}, got {len(cases)}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in cases) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "built",
        "suite": config.evaluation.suite,
        "version": config.knowledge_version,
        "cases": len(cases),
        "output": output.as_posix(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the deterministic 600-case RAG suite")
    parser.add_argument("--config", default="configs/rag/rag_v1.yaml")
    parser.add_argument("--output", type=Path, default=Path("evals/rag/rag_v1.jsonl"))
    args = parser.parse_args()
    print(json.dumps(build(args.config, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
