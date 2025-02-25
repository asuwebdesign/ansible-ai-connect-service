#  Copyright Red Hat
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import copy
import uuid
from unittest.mock import Mock, patch

from django.test import TestCase, modify_settings, override_settings
from yaml.error import Mark
from yaml.scanner import ScannerError

from ansible_ai_connect.ai.api.data.data_model import APIPayload
from ansible_ai_connect.ai.api.exceptions import PreprocessInvalidYamlException
from ansible_ai_connect.ai.api.pipelines.completion_context import CompletionContext
from ansible_ai_connect.ai.api.pipelines.completion_stages.pre_process import (
    PreProcessStage,
    completion_pre_process,
    task_prefix,
)
from ansible_ai_connect.ai.api.serializers import CompletionRequestSerializer


def add_indents(vars, n):
    return "\n".join([(" " * n + line) for line in vars.split("\n")])


######################################
# Test data for the playbook use case
######################################

VARS_1 = """\
mattermost_app:
  env: enhanced_context_value_env
  MM_TEAMSETTINGS_SITENAME: enhanced_context_value_MM_TEAMSETTINGS_SITENAME
  name: enhanced_context_value_name
  image: enhanced_context_value_image
  state: enhanced_context_value_state
  generate_systemd: enhanced_context_value_generate_systemd
  path: enhanced_context_value_path
  container_prefix: enhanced_context_value_container_prefix
  restart_policy: enhanced_context_value_restart_policy
  ports:
    - 8065:8065"""

VARS_2 = '''\
var_from_var_files_2:
  - password: ""
  - test:
      test1: ""
      test3:
        - foo
        - test4:
            test5: ""'''

VARS_3 = 'var_from_include_vars: ""'

PLAYBOOK_PAYLOAD = {
    "suggestionId": uuid.uuid4(),
    "prompt": """\
---
- hosts: all
  remote_user: root
  vars:
    favcolor: blue
  vars_files:
    - ./vars/external_vars_1.yml
    - ./vars/external_vars_2.yml
  tasks:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
    - name: Run container with podman using mattermost_app var
""",
    "metadata": {
        "documentUri": f"document-{uuid.uuid4()}",
        "ansibleFileType": "playbook",
        "activityId": uuid.uuid4(),
        "additionalContext": {
            "playbookContext": {
                "varInfiles": {
                    "./vars/external_vars_1.yml": VARS_1,
                    "./vars/external_vars_2.yml": VARS_2,
                },
                "roles": {},
                "includeVars": {
                    "/home/anouser/ansible/var_test/scenario_1/vars/external_vars_3.yml": VARS_3,
                },
            },
            "roleContext": {},
            "standaloneTaskContext": {},
        },
    },
}

#
# If the prompt is processed with the formatter with inserting variables, following context
# will be generated:
#
PLAYBOOK_CONTEXT_WITH_VARS = f"""\
- hosts: all
  remote_user: root
  vars:
    favcolor: blue
{add_indents(VARS_1, 4)}
{add_indents(VARS_2, 4)}
{add_indents(VARS_3, 4)}
  tasks:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
"""

PLAYBOOK_PAYLOAD_PROMPT_WITH_OVERLAPPING_EXISTING_VARS = """\
---
- hosts: all
  remote_user: root
  vars:
    # This whole var will be overwritten, both the overlapping key and unique key
    mattermost_app:
      image: playbook_value_image
      unique: playbook_value_unique
  vars_files:
    - ./vars/external_vars_1.yml
    - ./vars/external_vars_2.yml
  tasks:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
    - name: Run container with podman using mattermost_app var
"""

#
# If the prompt is processed with the formatter with overlapping existing and
# enhanced context variables, following context will be generated with enhanced context vars
# taking precedence over existing vars:
#
PLAYBOOK_CONTEXT_WITH_OVERLAPPING_EXISTING_VARS = f"""\
- hosts: all
  remote_user: root
  vars:
{add_indents(VARS_1, 4)}
{add_indents(VARS_2, 4)}
{add_indents(VARS_3, 4)}
  tasks:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
"""

PLAYBOOK_PAYLOAD_PROMPT_WITH_VARS_FILES_NOT_IN_ADDITIONAL_CONTEXT = """\
---
- hosts: all
  remote_user: root
  vars:
    # This whole var will be overwritten, both the overlapping key and unique key
    mattermost_app:
      image: playbook_value_image
      unique: playbook_value_unique
  vars_files:
    - ./vars/external_vars_1.yml
    - ./vars/external_vars_2.yml
    - ./vars/external_vars_not_included_in_additional_context.yml
  tasks:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
    - name: Run container with podman using mattermost_app var
"""

#
# If the prompt is processed with the formatter with vars_files not included in the additional
# context, only those files will remain in the vars_files section:
#
PLAYBOOK_CONTEXT_WITH_VARS_FILES_NOT_IN_ADDITIONAL_CONTEXT = f"""\
- hosts: all
  remote_user: root
  vars:
{add_indents(VARS_1, 4)}
{add_indents(VARS_2, 4)}
{add_indents(VARS_3, 4)}
  vars_files:
    - ./vars/external_vars_not_included_in_additional_context.yml
  tasks:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
"""


PLAYBOOK_PAYLOAD_PROMPT_WITH_NO_PREEXISTING_VARS = """\
---
- hosts: all
  remote_user: root
  vars_files:
    - ./vars/external_vars_1.yml
    - ./vars/external_vars_2.yml
  tasks:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
    - name: Run container with podman using mattermost_app var
"""

#
# If the prompt is processed with the formatter with inserting variables,
# with no other pre-existing vars, following context
# will be generated:
#
PLAYBOOK_CONTEXT_WITH_ONLY_ADDITIONAL_CONTEXT_VARS = f"""\
- hosts: all
  remote_user: root
  vars:
{add_indents(VARS_1, 4)}
{add_indents(VARS_2, 4)}
{add_indents(VARS_3, 4)}
  tasks:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
"""

PLAYBOOK_PAYLOAD_PROMPT_WITH_NO_PREEXISTING_VARS_AND_ONE_MULTITASK_PROMPT = """\
---
- hosts: all
  remote_user: root
  vars_files:
    - ./vars/external_vars_1.yml
    - ./vars/external_vars_2.yml
  tasks:
    # Include variable
"""

#
# If the prompt is processed with the formatter with inserting variables,
# with no other pre-existing vars, following context
# will be generated:
#
PLAYBOOK_CONTEXT_WITH_ONLY_ADDITIONAL_CONTEXT_VARS_AND_EMPTY_TASKS = f"""\
- hosts: all
  remote_user: root
  vars:
{add_indents(VARS_1, 4)}
{add_indents(VARS_2, 4)}
{add_indents(VARS_3, 4)}
  tasks:
"""

PLAYBOOK_PAYLOAD_PROMPT_WITH_HANDLERS_NO_PREEXISTING_VARS = """\
---
- hosts: all
  remote_user: root
  vars_files:
    - ./vars/external_vars_1.yml
    - ./vars/external_vars_2.yml
  handlers:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
    - name: Run container with podman using mattermost_app var
"""

#
# If the prompt is processed with the formatter with inserting variables,
# with no other pre-existing vars, following context
# will be generated:
#
PLAYBOOK_CONTEXT_WITH_HANDLERS_ONLY_ADDITIONAL_CONTEXT_VARS = f"""\
- hosts: all
  remote_user: root
  vars:
{add_indents(VARS_1, 4)}
{add_indents(VARS_2, 4)}
{add_indents(VARS_3, 4)}
  handlers:
    - name: Include variable
      ansible.builtin.include_vars:
        file: ./vars/external_vars_3.yml
"""
#
# If the prompt is processed with the formatter without inserting variables, following
# changes will be made to generate the context:
#
#   1. The first line ("---") will be removed, and,
#   2. The last line will be removed as the prompt
#
PLAYBOOK_CONTEXT_WITHOUT_VARS = "\n".join(PLAYBOOK_PAYLOAD["prompt"].split("\n")[1:-2]) + "\n"

#
# If the prompt is NOT processed with the formatter, we will see the first line of the original
# prompt ("---") in the context.
#
PLAYBOOK_CONTEXT_WITHOUT_FORMATTING = "\n".join(PLAYBOOK_PAYLOAD["prompt"].split("\n")[:-2]) + "\n"

PLAYBOOK_TWO_PLAYS_PAYLOAD = {
    "suggestionId": uuid.uuid4(),
    "prompt": """\
---
- hosts: all
  remote_user: root
  vars_files:
    - ./vars/external_vars_1.yml
  tasks:
    - name: Print hello
      ansible.builtin.debug:
        msg: Hello
- hosts: all
  remote_user: root
  vars_files:
    - ./vars/external_vars_1.yml
  tasks:
    - name: Print goodbye
""",
    "metadata": {
        "documentUri": f"document-{uuid.uuid4()}",
        "ansibleFileType": "playbook",
        "activityId": uuid.uuid4(),
        "additionalContext": {
            "playbookContext": {
                "varInfiles": {
                    "./vars/external_vars_1.yml": VARS_1,
                },
                "roles": {},
                "includeVars": {},
            },
            "roleContext": {},
            "standaloneTaskContext": {},
        },
    },
}

#
# If the prompt is processed with the formatter with inserting variables, following context
# will be generated:
#
PLAYBOOK_TWO_PLAYS_CONTEXT_WITH_VARS = f"""\
- hosts: all
  remote_user: root
  vars:
{add_indents(VARS_1, 4)}
  tasks:
    - name: Print hello
      ansible.builtin.debug:
        msg: Hello

- hosts: all
  remote_user: root
  vars:
{add_indents(VARS_1, 4)}
  tasks:
"""

###########################################
# Test data for the tasks_in_role use case
###########################################
VARS_4 = 'openvpn_role: ""'

VARS_5 = '''\
_openvpn_packages:
  server:
    - openvpn
    - easy-rsa
  client:
    - openvpn
openvpn_packages: ""
_openvpn_easyrsa_path:
  default: ""
  Debian: ""
openvpn_easyrsa_path: ""
_openvpn_group:
  default: ""
  Debian: ""
  RedHat: ""
_openvpn_configuration_directory:
  client:
    default: ""
    Debian: ""
    RedHat-7: ""
  server:
    default: ""
    Debian: ""
    RedHat-7: ""
openvpn_configuration_directory: ""
openvpn_group: ""
_openvpn_service:
  server:
    default: ""
    RedHat-7: ""
    RedHat: ""
    Ubuntu: ""
  client:
    default: ""
    RedHat-7: ""
    RedHat: ""
    Ubuntu: ""
openvpn_service: ""'''

TASKS_IN_ROLE_PAYLOAD = {
    "suggestionId": uuid.uuid4(),
    "prompt": """\
---
- name: import assert.yml
  ansible.builtin.import_tasks: assert.yml
  run_once: true
  delegate_to: localhost

- name: install openvpn packages
""",
    "metadata": {
        "documentUri": f"document-{uuid.uuid4()}",
        "ansibleFileType": "tasks_in_role",
        "activityId": uuid.uuid4(),
        "additionalContext": {
            "playbookContext": {},
            "roleContext": {
                "name": "",
                "tasks": [],
                "roleVars": {"defaults": {"main.yml": VARS_4}, "vars": {"main.yml": VARS_5}},
                "includeVars": {},
            },
            "standaloneTaskContext": {},
        },
    },
}

TASKS_IN_ROLE_CONTEXT_WITH_VARS = f"""\
- name: Set variables from context
  ansible.builtin.set_fact:
{add_indents(VARS_4, 4)}
{add_indents(VARS_5, 4)}

- name: import assert.yml
  ansible.builtin.import_tasks: assert.yml
  run_once: true
  delegate_to: localhost

"""

#
# If the prompt is processed with the formatter without inserting variables, following
# changes will be made to generate the context:
#
#   1. The first line ("---") will be removed, and,
#   2. The last line will be removed as the prompt
#
TASKS_IN_ROLE_CONTEXT_WITHOUT_VARS = (
    "\n".join(TASKS_IN_ROLE_PAYLOAD["prompt"].split("\n")[1:-2]) + "\n"
)

###################################
# Test data for the tasks use case
###################################
TASKS_PAYLOAD = {
    "suggestionId": uuid.uuid4(),
    "prompt": """\
---
- name: import assert.yml
  ansible.builtin.import_tasks: assert.yml
  run_once: true
  delegate_to: localhost

- name: install openvpn packages
""",
    "metadata": {
        "documentUri": f"document-{uuid.uuid4()}",
        "ansibleFileType": "tasks",
        "activityId": uuid.uuid4(),
        "additionalContext": {
            "playbookContext": {},
            "roleContext": {},
            "standaloneTaskContext": {"includeVars": {"main.yml": VARS_4}},
        },
    },
}

TASKS_CONTEXT_WITH_VARS = f"""\
- name: Set variables from context
  ansible.builtin.set_fact:
{add_indents(VARS_4, 4)}

- name: import assert.yml
  ansible.builtin.import_tasks: assert.yml
  run_once: true
  delegate_to: localhost

"""
#
# If the prompt is processed with the formatter without inserting variables, following
# changes will be made to generate the context:
#
#   1. The first line ("---") will be removed, and,
#   2. The last line will be removed as the prompt
#
TASKS_CONTEXT_WITHOUT_VARS = "\n".join(TASKS_PAYLOAD["prompt"].split("\n")[1:-2]) + "\n"

TASKS_PAYLOAD_PROMPT_WITH_QUOTED_TASKS = """\
---
- name: "import assert.yml"
  ansible.builtin.import_tasks: assert.yml
  run_once: true
  delegate_to: localhost

- name: "install openvpn packages"
"""

TASKS_PAYLOAD_PROMPT_WITH_NON_QUOTED_TASK = """\
- name: import assert.yml
  ansible.builtin.import_tasks: assert.yml
  run_once: true
  delegate_to: localhost

"""


@modify_settings()
class CompletionPreProcessTest(TestCase):

    @staticmethod
    def mock_context(payload, is_commercial_user) -> CompletionContext:
        original_prompt = payload.get("prompt")
        user = Mock(rh_user_has_seat=is_commercial_user)
        request = Mock(user=user)
        serializer = CompletionRequestSerializer(context={"request": request})
        data = serializer.validate(payload.copy())
        return CompletionContext(
            request=request,
            payload=APIPayload(
                prompt=data.get("prompt"),
                original_prompt=original_prompt,
                context=data.get("context"),
            ),
            metadata=data.get("metadata"),
        )

    def call_completion_validate_multitask_yaml(self, payload) -> []:
        context = CompletionPreProcessTest.mock_context(payload, True)

        with self.assertRaises(PreprocessInvalidYamlException) as e:
            PreProcessStage().process(context)

        exception: PreprocessInvalidYamlException = e.exception
        detail: dict = exception.detail
        code = detail["code"]
        messages = detail["message"]
        self.assertEqual(PreprocessInvalidYamlException.default_code, code)
        return messages

    def call_completion_pre_process(self, payload, is_commercial_user, expected_context):
        context = CompletionPreProcessTest.mock_context(payload, is_commercial_user)
        completion_pre_process(context)
        self.assertEqual(context.payload.context, expected_context)

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_commercial_user_and_feature_enabled(self):
        self.call_completion_pre_process(
            PLAYBOOK_PAYLOAD,
            True,
            PLAYBOOK_CONTEXT_WITH_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_feature_enabled_with_overlapping_vars(self):
        payload = copy.deepcopy(PLAYBOOK_PAYLOAD)
        payload["prompt"] = PLAYBOOK_PAYLOAD_PROMPT_WITH_OVERLAPPING_EXISTING_VARS

        self.call_completion_pre_process(
            payload,
            True,
            PLAYBOOK_CONTEXT_WITH_OVERLAPPING_EXISTING_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_vars_files_not_in_additional_context(self):
        payload = copy.deepcopy(PLAYBOOK_PAYLOAD)
        payload["prompt"] = PLAYBOOK_PAYLOAD_PROMPT_WITH_VARS_FILES_NOT_IN_ADDITIONAL_CONTEXT

        self.call_completion_pre_process(
            payload,
            True,
            PLAYBOOK_CONTEXT_WITH_VARS_FILES_NOT_IN_ADDITIONAL_CONTEXT,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_commercial_user_and_feature_enabled_with_no_preexisting_vars(
        self,
    ):
        payload = copy.deepcopy(PLAYBOOK_PAYLOAD)
        payload["prompt"] = PLAYBOOK_PAYLOAD_PROMPT_WITH_NO_PREEXISTING_VARS

        self.call_completion_pre_process(
            payload,
            True,
            PLAYBOOK_CONTEXT_WITH_ONLY_ADDITIONAL_CONTEXT_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_commercial_user_and_feature_enabled_with_no_preexisting_vars_and_one_multitask_prompt(  # noqa: E501
        self,
    ):
        payload = copy.deepcopy(PLAYBOOK_PAYLOAD)
        payload["prompt"] = (
            PLAYBOOK_PAYLOAD_PROMPT_WITH_NO_PREEXISTING_VARS_AND_ONE_MULTITASK_PROMPT
        )

        self.call_completion_pre_process(
            payload,
            True,
            PLAYBOOK_CONTEXT_WITH_ONLY_ADDITIONAL_CONTEXT_VARS_AND_EMPTY_TASKS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_commercial_user_and_feature_enabled_with_no_preexisting_vars_and_handlers(  # noqa: E501
        self,
    ):
        payload = copy.deepcopy(PLAYBOOK_PAYLOAD)
        payload["prompt"] = PLAYBOOK_PAYLOAD_PROMPT_WITH_HANDLERS_NO_PREEXISTING_VARS

        self.call_completion_pre_process(
            payload,
            True,
            PLAYBOOK_CONTEXT_WITH_HANDLERS_ONLY_ADDITIONAL_CONTEXT_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=False)
    def test_additional_context_with_commercial_user_and_feature_disabled(self):
        self.call_completion_pre_process(
            PLAYBOOK_PAYLOAD,
            True,
            PLAYBOOK_CONTEXT_WITHOUT_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_non_commercial_user(self):
        self.call_completion_pre_process(
            PLAYBOOK_PAYLOAD,
            False,
            PLAYBOOK_CONTEXT_WITHOUT_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_commercial_user_and_multi_task_prompt(self):
        payload = copy.deepcopy(PLAYBOOK_PAYLOAD)
        # Replace the last line of the prompt with a multi-task prompt that includes '&'
        payload["prompt"] = (
            "\n".join(payload["prompt"].split("\n")[:-2]) + "\n    # do this & do that\n"
        )
        self.call_completion_pre_process(
            payload,
            True,
            PLAYBOOK_CONTEXT_WITH_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_additional_context_with_playbook_with_two_plays(self):
        self.call_completion_pre_process(
            PLAYBOOK_TWO_PLAYS_PAYLOAD,
            True,
            PLAYBOOK_TWO_PLAYS_CONTEXT_WITH_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_tasks_inrole(self):
        self.call_completion_pre_process(
            TASKS_IN_ROLE_PAYLOAD,
            True,
            TASKS_IN_ROLE_CONTEXT_WITH_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=False)
    def test_tasks_inrole_with_feature_disabled(self):
        self.call_completion_pre_process(
            TASKS_IN_ROLE_PAYLOAD,
            True,
            TASKS_IN_ROLE_CONTEXT_WITHOUT_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_standalone_tasks(self):
        self.call_completion_pre_process(
            TASKS_PAYLOAD,
            True,
            TASKS_CONTEXT_WITH_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=False)
    def test_standalone_tasks_with_feature_disabled(self):
        self.call_completion_pre_process(
            TASKS_PAYLOAD,
            True,
            TASKS_CONTEXT_WITHOUT_VARS,
        )

    @override_settings(ENABLE_ADDITIONAL_CONTEXT=True)
    def test_other_ansible_type(self):
        payload = copy.deepcopy(TASKS_PAYLOAD)
        payload["metadata"]["ansibleFileType"] = "other"
        self.call_completion_pre_process(
            payload,
            True,
            TASKS_CONTEXT_WITHOUT_VARS,
        )

    def test_quoted_singletask(
        self,
    ):
        payload = copy.deepcopy(TASKS_PAYLOAD)
        payload["prompt"] = TASKS_PAYLOAD_PROMPT_WITH_QUOTED_TASKS

        self.call_completion_pre_process(
            payload,
            True,
            TASKS_PAYLOAD_PROMPT_WITH_NON_QUOTED_TASK,
        )

    def test_multitask_empty(self):
        payload = copy.deepcopy(TASKS_PAYLOAD)
        payload[
            "prompt"
        ] = """
        ---
        - name: Test the vscode extension
            hosts: all
            tasks:
                # install apache &
            """

        messages = self.call_completion_validate_multitask_yaml(payload)
        self.assertEqual(1, len(messages))
        self.assertIn("Empty task definition.", messages[0])

    def test_multitask_with_hyphen(self):
        payload = copy.deepcopy(TASKS_PAYLOAD)
        payload[
            "prompt"
        ] = """
        ---
        - name: Test the vscode extension
            hosts: all
            tasks:
                # - install apache
            """

        messages = self.call_completion_validate_multitask_yaml(payload)
        self.assertEqual(1, len(messages))
        self.assertIn(
            "Task '- install apache' invalid at column 1. Task starts with a hyphen.", messages[0]
        )

    def test_multitask_with_colon(self):
        payload = copy.deepcopy(TASKS_PAYLOAD)
        payload[
            "prompt"
        ] = """
        ---
        - name: Test the vscode extension
            hosts: all
            tasks:
                # install: apache
            """

        messages = self.call_completion_validate_multitask_yaml(payload)
        self.assertEqual(1, len(messages))
        self.assertIn(
            "Task 'install: apache' invalid at column 8. Task contains a colon.", messages[0]
        )

    def test_multitask_with_multiple_errors(self):
        payload = copy.deepcopy(TASKS_PAYLOAD)
        payload[
            "prompt"
        ] = """
        ---
        - name: Test the vscode extension
            hosts: all
            tasks:
                # install: apache & - start it &
            """

        messages = self.call_completion_validate_multitask_yaml(payload)
        self.assertEqual(3, len(messages))
        self.assertIn(
            "Task 'install: apache' invalid at column 8. Task contains a colon.", messages[0]
        )
        self.assertIn(
            "Task '- start it' invalid at column 1. Task starts with a hyphen.", messages[1]
        )
        self.assertIn("Empty task definition.", messages[2])

    @patch("yaml.safe_load")
    def test_multitask_with_scanner_error(self, mock_safe_load):
        payload = copy.deepcopy(TASKS_PAYLOAD)
        payload[
            "prompt"
        ] = """
        ---
        - name: Test the vscode extension
            hosts: all
            tasks:
                # Some crazy YAML
            """

        mock_safe_load.side_effect = Mock(
            side_effect=ScannerError(
                None,
                None,
                "Something went horribly wrong",
                Mark("name", 0, 0, len(task_prefix), None, 0),
            )
        )

        messages = self.call_completion_validate_multitask_yaml(payload)
        self.assertEqual(1, len(messages))
        self.assertIn(
            "Task 'Some crazy YAML' invalid at column 1. Something went horribly wrong.",
            messages[0],
        )
