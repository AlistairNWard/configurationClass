#!/bin/bash/python

from __future__ import print_function
import networkx as nx
from copy import deepcopy

import configurationClassErrors
from configurationClassErrors import *

import json
import os
import sys

class toolAttributes:
  def __init__(self):
    self.arguments     = {}
    self.argumentOrder = []
    self.description   = ''
    self.executable    = ''
    self.isHidden      = ''
    self.modifier      = ''
    self.path          = ''
    self.precommand    = ''

class toolConfiguration:
  def __init__(self):
    self.attributes           = {}
    self.availableTools       = {}
    self.configurationData    = {}
    self.errors               = configurationClassErrors()
    self.filename             = ''
    self.jsonError            = ''
    self.setRequiredArguments = False

  # Process the tool data.
  def processConfigurationData(self, tool, data):

    # Validate the information.
    success = self.validateConfigurationData(tool, data)

    # Include the tool in the list of available tools.
    self.availableTools[tool] = tool

    # Set the general tool attributes.
    attributes = toolAttributes()
    self.setToolAttribute(attributes, tool, 'description', self.configurationData[tool]['description'])
    self.setToolAttribute(attributes, tool, 'executable', self.configurationData[tool]['executable'])
    if 'modifier' in self.configurationData[tool]: self.setToolAttribute(attributes, tool, 'modifier', self.configurationData[tool]['modifier'])
    self.setToolAttribute(attributes, tool, 'path', self.configurationData[tool]['path'])
    if 'precommand' in self.configurationData[tool]: self.setToolAttribute(attributes, tool, 'precommand', self.configurationData[tool]['precommand'])
    if 'hide tool' in self.configurationData[tool]: self.setToolAttribute(attributes, tool, 'isHidden', self.configurationData[tool]['hide tool'])

    # Look to see if the 'argument order' section is present.
    if 'argument order' in self.configurationData[tool]:
      self.setToolAttribute(attributes, tool, 'argumentOrder', self.configurationData[tool]['argument order'])
    self.attributes[tool] = attributes

    return data['instances']

  # Validate the contents of the tool configuration file.
  def validateConfigurationData(self, tool, data):
    self.configurationData[tool] = data

    # TODO In the validation, ensure that if the argument is for a filename stub, make sure that 
    # if constructing filename, the replace extension is set to False.  Otherwise, the algorithm
    # will look for extensions to use which will not exist!
    return True

  def getToolAttribute(self, tool, attribute):
    try: value = getattr(self.attributes[tool], attribute)
    except:

      if tool not in self.attributes:
        #TODO ERROR
        print('MISSING TOOL: getToolAttribute', tool)
        self.errors.terminate()

      else:
        #TODO ERROR
        print('MISSING ATTRIBUTE: getToolAttribute', attribute)
        self.errors.terminate()

    return value

  # TODO CHECK THIS. GET INFO USING getToolAttribute.
  # Get information from the configuration file (not argument data).
  def getConfigurationData(self, tool, attribute):
    try: value = self.configurationData[tool][attribute]
    except:

      #FIXME
      if tool not in self.configurationData:
        print('MISSING TOOL: tools.getConfigurationData', tool)
        self.errors.terminate()
  
      # If the attribute cannot be found, return None.
      # TODO Include a check that the attribute is valid. Want to return None for
      # cases where attribute is allowed, but not present (e.g. precommand).
      if attribute not in self.configurationData[tool]: return None

    return value
 
  # Get information about a tool argument from the configuration data.
  def getArgumentData(self, tool, argument, attribute):
    try: value = self.configurationData[tool]['arguments'][argument][attribute]
    except:

      #FIXME Sort all the errors.
      if tool not in self.configurationData:
        print('MISSING TOOL: tools.getArgumentData', tool)
        self.errors.terminate()

      if argument not in self.configurationData[tool]['arguments']:
        print('MISSING ARGUMENT: tools.getArgumentData', tool, argument, attribute)
        for argument in self.configurationData[tool]['arguments']: print(argument)
        self.errors.terminate()

      if attribute not in self.configurationData[tool]['arguments'][argument]:
        return None

    return value

  # Get a value in the toolAttributes.
  def getToolAttribute(self, tool, attribute):
    try: value = getattr(self.attributes[tool], attribute)

    # If the attribute can't be found, determine the source of the problem and provide an
    # error message.
    except:

      #TODO ERROR
      if tool not in self.attributes:
        print('MISSING TOOL - tools.getToolAttribute')
        self.errors.terminate()

      # If the attribute doesn't exist, return None.
      return None

    return value

  # Set a value in the toolAttributes.
  def setToolAttribute(self, attributes, tool, attribute, value):
    try: test = getattr(attributes, attribute)

    # If the attribute can't be set, determine the source of the problem and provide an
    # error message.
    except:

      # If the tool is not available.TODO
      self.errors.invalidAttributeInSetToolAttribute(attribute)
      self.errors.terminate()

    # Set the attribute.
    setattr(attributes, attribute, value)

  # Get the long form of a tool argument.
  def getLongFormArgument(self, tool, argument):
    try: value = self.configurationData[tool]['arguments'][argument]['shortForm']
    except:

      #FIXME Sort all the errors.
      if tool not in self.configurationData:
        print('MISSING TOOL: tools.getLongFormArgument', tool)
        self.errors.terminate()

      # If the argument is not in the configurationData structure, this might be because
      # the short form of the argument was supplied.
      if argument not in self.configurationData[tool]['arguments']:
        for toolArgument in self.configurationData[tool]['arguments']:
          shortForm = self.getArgumentData(tool, toolArgument, 'short form argument')
          if shortForm == argument: return toolArgument

        # If all the short form arguments for this tool were searched and none of them
        # were the supplied argument, the argument is not valid for this tool.
        print('tools.getLongFormArgument: invalid argument,', argument)
        self.errors.terminate()

    # If the supplied argument was already the long form version, return the original
    # argument.
    return argument