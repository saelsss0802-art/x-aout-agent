from __future__ import annotations

from core.db import SessionLocal
from core.models import Account, AccountKnowledge, AccountType, Agent, AgentStatus, Post, PostType, TargetAccount


def main() -> None:
    with SessionLocal.begin() as session:
        account = Account(
            name="Demo Account",
            type=AccountType.business,
            api_keys={"x": "demo-key"},
            media_assets_path="/data/media",
        )
        session.add(account)
        session.flush()

        agent = Agent(
            account_id=account.id,
            status=AgentStatus.active,
            feature_toggles={"autopost": True},
        )
        session.add(agent)
        session.flush()

        session.add(
            AccountKnowledge(
                account_id=account.id,
                persona="Data-driven social media strategist",
                tone="Professional and concise",
                strategy="Test hooks and optimize with engagement metrics",
                ng_items=["politics", "misinformation"],
                reference_accounts=["@example"],
            )
        )

        target_account = TargetAccount(
            agent_id=agent.id,
            handle="@target_example",
            like_limit=10,
            reply_limit=3,
            quote_rt_limit=2,
        )
        session.add(target_account)

        session.add(
            Post(
                agent_id=agent.id,
                content="Demo launch post",
                type=PostType.tweet,
                media_urls=[],
            )
        )
    print("seed completed")


if __name__ == "__main__":
    main()
