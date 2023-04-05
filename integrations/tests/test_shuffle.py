# Copyright (C) 2015, Wazuh Inc.
# Created by Wazuh, Inc. <info@wazuh.com>.
# This program is free software; you can redistribute
# it and/or modify it under the terms of GPLv2

"""Unit tests for shuffle.py integration."""

import sys
import os
import json
import logging
import pytest
import requests
import shuffle
from unittest.mock import patch, mock_open, MagicMock

sys.path.append(os.path.join(os.path.dirname(
    os.path.realpath(__file__)), '..', '..'))

alert_template = {'timestamp': 'year-month-dayThours:minuts:seconds+0000',
                  'rule': {'level': 0, 'description': 'alert description',
                           'id': '',
                           'firedtimes': 1},
                  'id': 'alert_id',
                  'full_log': 'full log.', 'decoder': {'name': 'decoder-name'},
                  'location': 'wazuh-X'}

msg_template = '{"severity": 1, "pretext": "WAZUH Alert", "title": "alert description", "text": "full log.", ' \
               '"rule_id": "rule-id", "timestamp": "year-month-dayThours:minuts:seconds+0000", "id": "alert_id", ' \
               '"all_fields": {"timestamp": "year-month-dayThours:minuts:seconds+0000", "rule": {"level": 0, ' \
               '"description": "alert description", "id": "rule-id", "firedtimes": 1}, "id": "alert_id", "full_log": ' \
               '"full log.", "decoder": {"name": "decoder-name"}, "location": "wazuh-X"}}'

sys_args_template = ['/var/ossec/integrations/shuffle.py', '/tmp/shuffle-XXXXXX-XXXXXXX.alert', '',
                     'http://<IP>:3001/api/v1/hooks/<HOOK_ID>']


def test_main_bad_arguments_exit():
    """Test that main function exits when wrong number of arguments are passed."""
    with patch("shuffle.open", mock_open()), \
            pytest.raises(SystemExit) as pytest_wrapped_e:
        shuffle.main(sys_args_template[0:2])
    assert pytest_wrapped_e.value.code == 2


def test_main_exception():
    """Test exception handling in main when process_args raises an exception."""
    with patch("shuffle.open", mock_open()), \
            patch('shuffle.process_args') as process, \
            pytest.raises(Exception):
        process.side_effect = Exception
        shuffle.main(sys_args_template)


def test_main():
    """Test the correct execution of the main function."""
    with patch("shuffle.open", mock_open()), \
            patch('json.load', return_value=alert_template), \
            patch('requests.post', return_value=requests.Response), \
            patch('shuffle.process_args') as process:
        shuffle.main(sys_args_template)
        process.assert_called_once_with(sys_args_template[1], sys_args_template[3])


@pytest.mark.parametrize('file_path, side_effect', [
    ('/tmp/non-existent', FileNotFoundError),
    (sys_args_template[1], json.decoder.JSONDecodeError("Expecting value", "", 0))
])
def test_process_args_exit(file_path, side_effect):
    """Test the process_args function exit codes.

    Parameters
    ----------
    side_effect : Exception
        Exception to be raised when there is a failure inside the Load alert section try.
    return_value : int
        Value to be returned when sys.exit() is invoked.
    """
    with patch("shuffle.open", mock_open()), \
            patch('json.load') as json_load, \
            pytest.raises(SystemExit) as pytest_wrapped_e:
        json_load.side_effect = side_effect
        shuffle.main((file_path, ''))
    assert pytest_wrapped_e.value.code == 2


def test_process_args():
    """Test the correct execution of the process_args function."""
    with patch("shuffle.open", mock_open()) as alert_file, \
            patch('json.load', return_value=alert_template), \
            patch('shuffle.send_msg') as send_msg, \
            patch('shuffle.generate_msg', return_value=msg_template) as generate_msg, \
            patch('requests.post', return_value=requests.Response):
        shuffle.process_args(sys_args_template[1], sys_args_template[3])
        alert_file.assert_called_once_with(sys_args_template[1], encoding='utf-8')
        generate_msg.assert_called_once_with(alert_template)
        send_msg.assert_called_once_with(msg_template, sys_args_template[3])


def test_process_args_not_sending_message():
    """Test that the send_msg function is not executed due to empty message after generate_msg."""
    with patch("shuffle.open", mock_open()), \
            patch('json.load', return_value=alert_template), \
            patch('shuffle.send_msg') as send_msg, \
            patch('shuffle.generate_msg', return_value=''):
        shuffle.process_args(sys_args_template[1], sys_args_template[3])
        send_msg.assert_not_called()


def test_logger(caplog):
    """Test the correct execution of the logger."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch('requests.post', return_value=mock_response):
        with caplog.at_level(logging.DEBUG, logger='shuffle'):
            args = sys_args_template
            args.append('info')
            shuffle.main(args)

    # Assert console log correctness
    assert caplog.records[0].message == 'Starting'
    assert caplog.records[1].message == f'Alerts file location: {sys_args_template[1]}'
    assert caplog.records[2].message == f'Webhook: {sys_args_template[3]}'
    assert caplog.records[-1].levelname == 'INFO'
    assert "DEBUG" not in caplog.text
    # Assert the log file is created and is not empty
    assert os.path.exists(shuffle.LOG_FILE)
    assert os.path.getsize(shuffle.LOG_FILE) > 0


@pytest.mark.parametrize('rule_id, expected_msg', [
    (shuffle.SKIP_RULE_IDS[0], ""),
    ('rule-id', msg_template)
])
def test_generate_msg(expected_msg, rule_id):
    """Test that the expected message is generated when json_alert received.

    Parameters
    ----------
    expected_msg : str
        Message that should be returned by the generate_msg function.
    rule_id : str
        ID of the rule to be processed.
    """

    alert_template['rule']['id'] = rule_id
    assert shuffle.generate_msg(alert_template) == expected_msg


@pytest.mark.parametrize('rule_level, severity', [
    (3, 1),
    (5, 2),
    (7, 2),
    (8, 3)
])
def test_generate_msg_severity(rule_level, severity):
    """Test that the different rule levels generate different severities in the message delivered by generate_msg.

    Parameters
    ----------
    rule_level: int
        Integer that represents the rule level.
    severity: int
        Expected severity level for the corresponding rule level.
    """

    alert_template['rule']['level'] = rule_level
    assert json.loads(shuffle.generate_msg(alert_template))['severity'] == severity


@pytest.mark.parametrize('rule_id, result', [
    (shuffle.SKIP_RULE_IDS[0], False),
    ('rule-id', True)
])
def test_filter_msg(rule_id, result):
    """Test the filter_msg function.

    Parameters
    ----------
    rule_id: str
        String that represents the alert rule ID to be checked.
    result: bool
        Expected result of the filter_msg function for the given alert.
    """

    alert_template['rule']['id'] = rule_id
    assert result == shuffle.filter_msg(alert_template)


def test_send_msg_raise_exception():
    """Test that the send_msg function will raise an exception when passed the wrong webhook url."""
    with patch('requests.post') as request_post, \
            pytest.raises(requests.exceptions.ConnectionError):
        request_post.side_effect = requests.exceptions.ConnectionError
        shuffle.send_msg(msg_template, 'http://webhook-url')


def test_send_msg():
    """Test that the send_msg function works as expected."""
    headers = {'content-type': 'application/json', 'Accept-Charset': 'UTF-8'}
    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch('requests.post', return_value=mock_response) as request_post:
        shuffle.send_msg(msg_template, sys_args_template[3])
        request_post.assert_called_once_with(sys_args_template[3], data=msg_template, headers=headers, verify=False)
