# effaced

GDPR data-subject mechanisms — export (Art. 15), erasure (Art. 17), consent
(Art. 7), and an append-only audit trail — across your own database **and**
the external systems you actually use.

**We ship the mechanisms. You own the compliance.**

This is the core package. See the [repository README](https://github.com/jaylann/effaced)
for the full story, quickstart, and resolver packages.

```bash
uv add effaced
```

```python
from sqlalchemy.orm import Mapped, mapped_column
from effaced import PiiCategory, pii, subject_link

class User(Base):
    __tablename__ = "users"
    __table_args__ = {"info": subject_link("")}

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(info=pii(PiiCategory.CONTACT))
```

> **Not legal advice.** effaced provides technical mechanisms for
> implementing data-subject rights. It does not make you GDPR-compliant
> and does not constitute legal advice.

Licensed under Apache-2.0.
