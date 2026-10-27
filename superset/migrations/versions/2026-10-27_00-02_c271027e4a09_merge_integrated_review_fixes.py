# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Merge the integrated AI review migration revisions.

Revision ID: c271027e4a09
Revises: c261026b1f09, b271027c9e14
Create Date: 2026-10-27 00:02:00.000000

"""

revision = "c271027e4a09"
down_revision = ("c261026b1f09", "b271027c9e14")


def upgrade() -> None:
    """Join the revisions without changing schema or data."""


def downgrade() -> None:
    """Restore the parent revisions without changing schema or data."""
