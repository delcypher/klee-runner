# Copyright (c) 2016, Daniel Liew
# This file is covered by the license in LICENSE
# vim: set sw=4 ts=4 softtabstop=4 expandtab:
from . import util
import collections
import copy
import os
import pprint
import yaml
import jsonschema


class ResultInfo:

    def __init__(self, data):
        assert isinstance(data, dict)
        self._data = data

# TODO: Implement property getters

    def isError(self):
        return 'error' in self._data

    def GetInternalRepr(self):
        return self._data


class ResultInfoValidationError(Exception):

    def __init__(self, message, absoluteSchemaPath=None):
        assert isinstance(message, str)
        if absoluteSchemaPath != None:
            assert isinstance(absoluteSchemaPath, collections.deque)
        self.message = message
        self.absoluteSchemaPath = absoluteSchemaPath

    def __str__(self):
        return self.message


def loadResultInfos(openFile):
    resultInfos = loadRawResultInfos(openFile)
    resultInfoObjects = []
    for r in resultInfos['results']:
        resultInfoObjects.append(ResultInfo(r))
    return resultInfoObjects


def loadRawResultInfos(openFile):
    resultInfos = util.loadYaml(openFile)
    validateResultInfos(resultInfos)
    return resultInfos


def getSchema():
    """
      Return the Schema for ResultInfo files.
    """
    yamlFile = os.path.join(os.path.dirname(__file__), 'ResultInfoSchema.yml')
    schema = None
    with open(yamlFile, 'r') as f:
        schema = util.loadYaml(f)
    assert isinstance(schema, dict)
    assert '__version__' in schema
    return schema


def validateResultInfos(resultInfos, schema=None):
    """
      Validate a ``resultInfo`` file.
      Will throw a ``ResultInfoValidationError`` exception if
      something is wrong
    """
    assert isinstance(resultInfos, dict)
    if schema == None:
        schema = getSchema()
    assert isinstance(schema, dict)
    assert '__version__' in schema

    # Even though the schema validates this field in the resultInfo we need to
    # check them ourselves first because if the schema version we have doesn't
    # match then we can't validate using it.
    if 'schema_version' not in resultInfos:
        raise ResultInfoValidationError(
            "'schema_version' is missing")
    if not isinstance(resultInfos['schema_version'], int):
        raise ResultInfoValidationError(
            "'schema_version' should map to an integer")
    if not resultInfos['schema_version'] >= 0:
        raise ResultInfoValidationError(
            "'schema_version' should map to an integer >= 0")
    if resultInfos['schema_version'] != schema['__version__']:
        raise ResultInfoValidationError(
            ('Schema version used by benchmark ({}) does not match' +
             ' the currently support schema ({})').format(
                resultInfos['schema_version'],
                schema['__version__']))

    # Validate against the schema
    try:
        jsonschema.validate(resultInfos, schema)
    except jsonschema.exceptions.ValidationError as e:
        raise ResultInfoValidationError(
            str(e),
            e.absolute_schema_path)
    return


def upgradeResultInfosToVersion(resultInfos, schemaVersion):
    """
      Upgrade invocation info to a particular schemaVersion. This
      does not validate it against the schema.
    """
    assert isinstance(resultInfos, dict)
    assert isinstance(schemaVersion, int)
    schemaVersionUsedByInstance = resultInfos['schema_version']
    assert isinstance(schemaVersionUsedByInstance, int)
    assert schemaVersionUsedByInstance >= 0
    assert schemaVersion >= 0
    newResultInfo = copy.deepcopy(resultInfos)

    if schemaVersionUsedByInstance == schemaVersion:
        # Nothing todo
        return newResultInfo
    elif schemaVersionUsedByInstance > schemaVersion:
        raise Exception(
            'Cannot downgrade benchmark specification to older schema')

    # TODO: Implement upgrade if we introduce new schema versions
    # We would implement various upgrade functions (e.g. ``upgrade_0_to_1()``, ``upgrade_1_to_2()``)
    # and call them successively until the ``resultInfos`` has been upgraded
    # to the correct version.
    raise NotImplementedException()


def upgradeResultInfosToSchema(resultInfos, schema=None):
    """
      Upgrade a ``invocationInfo`` to the specified ``schema``.
      The resulting ``invocationInfo`` is validated against that schema.
    """
    if schema == None:
        schema = getSchema()
    assert '__version__' in schema
    assert 'schema_version' in resultInfos

    newResultInfos = upgradeResultInfosToVersion(
        resultInfos,
        schema['__version__']
    )

    # Check the upgraded resultInfos against the schema
    validateResultInfos(newResultInfos, schema=schema)
    return newResultInfos
