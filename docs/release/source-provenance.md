# Source provenance and release gate

## Recorded facts at M0

| Field | Recorded value |
| --- | --- |
| Review date | 2026-08-03 |
| Imported upstream | `https://gitee.com/cswl2020/pic` |
| Local source archive reviewed | `pic-main.zip`, 49,616,526 bytes |
| Source archive SHA-256 | `26A59D1409E01EDC887A4CF810A09D5073CC81A1DF7005794F1A3F2C7A4554C2` |
| Source archive timestamp | 2026-08-03 07:22:17 UTC (local filesystem metadata) |
| Current version-control repository | `https://github.com/chendaxian318/imageeditor` |
| Local baseline commit | `96c1a2a71b45380fee80febcf163340ef8a070a6` |
| Existing baseline tag at review time | `v0.1.0-baseline` |
| Commit author metadata | `THEGodzilla <1104890480@qq.com>` |
| Upstream licence found in imported tree | None |
| Written assignment/redistribution grant stored in repository | None found |

The reviewed archive contained the application sources, build script, tests,
README and two release executables, but no `LICENSE`, `COPYING` or `NOTICE`
entry. Its hash is retained as evidence of exactly which archive was inspected;
it does not prove where the archive came from or who owns its contents. The
local baseline commit also contains changes made after extraction and is not
represented as a byte-for-byte archival mirror of that ZIP.

The recorded Git author, Gitee account, GitHub account and repository owner
names are technical metadata only. They do not, by themselves, establish the
identity of the author, ownership of copyright, an employment assignment, or
permission to relicense and distribute the imported code.

## Required evidence before public distribution

The release owner must collect and retain evidence answering all of the
following:

1. Who wrote each imported first-party source file and asset?
2. Was the work created for an employer, customer, school, or other entity
   that may own it?
3. Did every contributor agree to the proposed licence and commercial/public
   redistribution?
4. Were any files copied from tutorials, other repositories, generated tools,
   contractors, or AI systems with separate terms?
5. Are names, logos, icons, sample images, fonts and translations cleared for
   the intended use?
6. Does the written grant cover modification, executable distribution,
   sublicensing, commercial use, and all target territories?

Acceptable evidence may include signed assignments, contributor licence
agreements, employment/IP agreements, or a written licence grant from the
verified rights holder. Store evidence outside the public repository if it
contains personal or confidential information, and record a durable reference
and reviewer approval here.

## Gate decision

**Status: BLOCKED for public or commercial distribution.**

Internal engineering and testing may proceed only to the extent separately
authorised by the relevant rights holder or applicable law. A maintainer must
not replace the conservative root `LICENSE` with MIT, Apache-2.0, GPL, or a
proprietary end-user licence until the chain of title and authority to apply
that licence have been verified.

To clear the gate, add a dated decision record containing:

- verified rights holder;
- evidence reference;
- scope of granted rights;
- approved first-party licence;
- approver name/role; and
- any distribution restrictions.
