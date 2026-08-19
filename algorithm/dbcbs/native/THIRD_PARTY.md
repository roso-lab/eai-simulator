# Vendored Source Provenance

The native db-CBS target contains selected upstream source with EAI build and
runtime patches. It is intentionally vendored so a simulator run never loads
code from another checkout.

| Component | Upstream | Base revision | Vendored location | License |
| --- | --- | --- | --- | --- |
| db-CBS | `https://github.com/IMRCLab/db-CBS.git` | `220fc05c8f9dfe41ce02fa0d6bffea9eb71886a6` | `src/` | MIT; `licenses/db-CBS-LICENSE` |
| Kinodynamic CBS OMPL fork | `https://github.com/IMRCLab/Kinodynamic-Conflict-Based-Search.git` | `a30772771cbf491676ca05ec04cd6646b4bad9c5` | `ompl/` | BSD-3-Clause; `licenses/OMPL-LICENSE` |
| dynoplan | `https://github.com/quimortiz/dynoplan.git` | `4528dac35bf51ca46380e901b2273de1c3b33a03` | `dynoplan/` | MIT; `licenses/dynoplan-LICENSE` |
| dynobench | `https://github.com/quimortiz/dynobench.git` | `05bafb374e5b00e858d351e2e89d8f4b409f56ab` | `dynoplan/dynobench/` | MIT; `licenses/dynobench-LICENSE` |
| nlohmann/json 3.11.2 | `https://github.com/nlohmann/json.git` | `5d2754306d67d1e654a1a34e1d2e74439a9d53b3` | `dynoplan/dynobench/deps/json/` | MIT; `licenses/nlohmann-json-LICENSE` |

The OMPL tree is the fork under the Kinodynamic CBS `src/ompl` directory, not
an independently selected stock OMPL release. The repository CMake files and
selected db-CBS/dynoplan/dynobench sources contain EAI integration patches;
the revisions above identify their upstream bases rather than claiming the
vendored files are byte-for-byte copies.

## Motion Primitive

`motions/double_integrator_0_sorted.msgpack` comes from the motion archive
linked by the upstream db-CBS README:

`https://tubcloud.tu-berlin.de/s/CijbRaJadf6JwH3/download`

The maintained payload is 1,192,789 bytes. Its SHA-256 is:

```text
66b6a39765d554105d9ecd6b1bd2244673568e116c749871fe8936338d83454e
```

The upstream repository ignores the downloaded motion archive and does not
state a separate data license for it. EAI therefore does not redistribute the
payload through Git or Git LFS. `algorithm/dbcbs/fetch_motion_primitives.py`
downloads the single file directly from the upstream TUB Cloud WebDAV share
and verifies the size and SHA-256 before installation. The source repository
maintains the downloader, upstream revisions, license texts, and integrity
metadata; TUB Cloud remains the payload provider. Do not infer the payload's
terms from the MIT license that covers the db-CBS source code.

## Updating

Do not refresh this tree with an unreviewed directory copy. Record new base
revisions, preserve or update all applicable license texts, review the EAI
patch delta, rebuild from an empty ignored `build/` directory, and update the
motion hash if the maintained primitive changes.
