#!/bin/bash/python

from __future__ import print_function
import networkx as nx
from copy import deepcopy

import configurationClassErrors
from configurationClassErrors import *

import edgeAttributes
from edgeAttributes import *

import fileOperations
from fileOperations import *

import parameterSets
from parameterSets import *

import nodeAttributes
from nodeAttributes import *

import pipelineAttributes
from pipelineAttributes import *

import toolAttributes
from toolAttributes import *

import json
import os
import sys

class configurationMethods:
  def __init__(self):

    # Define methods for handling nodes and edges.
    self.edgeMethods = edgeClass()
    self.nodeMethods = nodeClass()

    # Define a class for handling parameter sets.
    self.parameterSets = parameterSetConfiguration()

    # Define the errors class.
    self.errors = configurationClassErrors()

    # Define operations for operating on files.
    self.fileOperations = fileOperations()

    # Define a class for handling pipelines.
    self.isPipeline = False
    self.pipeline   = pipelineConfiguration()

    # Define a class for handling tools.
    self.tools = toolConfiguration()

    # If any of the tools have a command to be evaluated at execution time, set
    # hasCommandToEvaluate to True.
    self.hasCommandToEvaluate = False

    self.nodeIDs = {}

  # Build a graph for an individual task.  The pipeline is built by merging nodes between
  # different tasks.  This step is performed later.
  def buildTaskGraph(self, graph, tasks):
    for task in tasks:
      tool = self.pipeline.taskAttributes[task].tool

      # Generate the task node.
      self.nodeMethods.buildTaskNode(graph, self.tools, self.pipeline, task, tool)

      # Find all required arguments for this task and build nodes for them all.  Link these nodes
      # to the task node.
      self.nodeMethods.buildRequiredPredecessorNodes(graph, self.tools, self.pipeline, task)

  # Assign values from the nodes section of the pipeline configuration file to the nodes.
  def assignPipelineAttributes(self, graph, tasks):

    # For nodes in the pipeline configuration file, find any that have the extension field.
    for nodeName in self.pipeline.linkedExtension:
      for task, argument in self.pipeline.commonNodes[nodeName]:
        nodeID = self.nodeMethods.getNodeForTaskArgument(graph, task, argument, 'option')[0]
        self.nodeMethods.setGraphNodeAttribute(graph, nodeID, 'linkedExtension', self.pipeline.linkedExtension[nodeName])

  # Merge shared nodes between tasks using information from the pipeline configuration file.  For
  # example, task A outputs a file fileA and taskB uses this as input.  Having built an individual
  # graph for each task, there exists an output file node for taskA and an input file node for
  # taskB (and also option nodes defining the names), but these are the same file and so these
  # nodes can be merged into a single node.
  def mergeNodes(self, graph):

    # The first step involves parsing through the 'nodes' section of the pipeline configuration file and
    # determining which option nodes will be merged.  For each set of option nodes to be merged, one is
    # picked to be kept and the others are marked for deletion.
    edgesToCreate = self.identifyNodesToRemove(graph)

    # Before creating all of the new edges, find any nodes that have not been created but were called
    # on to be merged.  Create the nodes and update the edgesToCreate structure with the new ID.
    self.createMissingMergedNodes(graph, edgesToCreate)

    # Parse through all of the edges that need to be created.  Create nodes where necessary and handle
    # cases where the option is a filename stub and there are multiple file nodes to handle.
    self.createEdgesForMergedNodes(graph, edgesToCreate)

    # Now add edges for the file nodes.
    self.createEdgesForMergedFileNodes(graph, edgesToCreate)

    # Having completed the merging process, purge the nodes that are no longer required.
    self.nodeMethods.purgeNodeMarkedForRemoval(graph)

    # Parse through all of the nodes that have been merged and check if they have files that
    # were marked in the pipeline configuration file as files that should be kept. If such
    # nodes exist, mark them.
    self.markNodesWithFilesToBeKept(graph)

    # Check that all tasks using files from filename stubs are connected to the correct nodes.
    self.checkEdges(graph)

    # Mark all edges that are greedy. If the input to a particular task is several sets of data, there
    # are two possible ways to handle this. The default is that the task will be run multiple times for
    # each set of data. However, if the task argument accepting the multiple sets of data is listed as
    # being 'greedy', all of the iterations will be collapsed into a single input and all of the files
    # will be used as input to a single run of the task.
    self.markGreedyEdges(graph)

  # Process all of the additional edges included in the pipeline configuration and add them to the
  # graph.
  def processOriginatingEdges(self, graph):
    isEdgesAdded = False

    for task in self.pipeline.originatingEdges:
      for argument in self.pipeline.originatingEdges[task]:

        # Check that th argument is valid.
        tool         = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
        configNodeID = self.pipeline.originatingConfigID[task][argument]
        if not argument in self.tools.getArguments(tool): self.errors.invalidArgumentInOriginatingEdges(task, tool, argument, configNodeID)

        # Get the node ID for this task/argument.
        nodeIDs = self.nodeMethods.getNodeForTaskArgument(graph, task, argument, 'option')

        # Loop over all the targets.
        for targetTask, targetArgument in self.pipeline.originatingEdges[task][argument]:

          # If there are edges to add, update the isEdgesAdded flag.
          isEdgesAdded = True

          # Loop over all the source node IDs and link to all target node IDs.
          for sourceNodeID in nodeIDs:

            # Since these are originating edges, the target task argument must be for an input file. Check
            # that this is the case and terminate if it isn't.
            targetNodeIDs = self.nodeMethods.getNodeForTaskArgument(graph, targetTask, targetArgument, 'option')
            for targetNodeID in targetNodeIDs:
              isInput = self.edgeMethods.getEdgeAttribute(graph, targetNodeID, targetTask, 'isInput')
              if not isInput: print('ERROR - config.processOriginatingEdges - Not an input.'); exit(0)

            # TODO Handle filename stubs.
            # Terminate if this is a filename stub. 
            isFilenameStub = self.edgeMethods.getEdgeAttribute(graph, sourceNodeID, task, 'isFilenameStub')
            if isFilenameStub: print('ERROR - config.processOriginatingEdges - Not handled target filename stubs'); exit(0)

            self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, sourceNodeID, targetTask, targetArgument, isOriginatingEdge = True)

            # Also link associated file nodes.
            for fileNodeID in self.nodeMethods.getAssociatedFileNodeIDs(graph, sourceNodeID):
              attributes                   = deepcopy(graph[targetNodeIDs[0]][targetTask]['attributes'])
              attributes.isOriginatingEdge = True
              graph.add_edge(fileNodeID, targetTask, attributes = attributes)

    return isEdgesAdded

  # Parse through the 'nodes' section of the pipeline configuration file and identify which nodes can be
  # removed (i.e. merged with another node).  The nodes to be removed are tagged as to be removed and the
  # node that will replace them is also stored.
  def identifyNodesToRemove(self, graph):
    missingNodeID = 1

    # Create a dictionary to store the tasks and arguments required to build edges from the
    # merged node.  
    edgesToCreate = {}
    for configNodeID in self.pipeline.commonNodes:

      # Check if there are files associated with this node that should be deleted.
      deleteFiles = self.pipeline.nodeAttributes[configNodeID].deleteFiles

      # If there is only a single node listed, there is no need to proceed, since no merging needs to
      # take place. 
      optionsToMerge   = deepcopy(self.pipeline.commonNodes[configNodeID])
      #TODO CHECK
      linkedExtensions = deepcopy(self.pipeline.linkedExtension[configNodeID]) if configNodeID in self.pipeline.linkedExtension else {}
      if len(optionsToMerge) != 1:

        # Pick one of the nodes to keep.  If the option picked has not yet been set as a node, choose
        # again.
        nodeID           = None
        absentNodeValues = []
        #TODO CHECK
        isNodeSet = False
        #while nodeID == None and optionsToMerge:
        #while optionsToMerge and not isNodeSet:
        for counter, (task, argument) in enumerate(optionsToMerge):
          #TODO CHECK isNodeSet
          isNodeSet    = True
          #optionToKeep = optionsToMerge.pop(0)
          #task         = optionToKeep[0]
          #argument     = optionToKeep[1]
          nodeID       = self.nodeMethods.doesNodeExist(graph, task, argument)

          #TODO CHECK
          # Check to see if the node selected also has instructions on connection to an extension. If
          # so, this node should not be the selected node.
          if task in linkedExtensions:
            if argument in linkedExtensions[task]: isNodeSet = False

          # Store the ID of the node being kept along with the value it was given in the common nodes
          # section of the configuration file.  The parameter sets information will refer to the common
          # node value and this needs to point to the nodeID in the graph.
          self.nodeIDs[configNodeID] = nodeID

          # If the node doesn't exist, store the task and argument.  These will be put into the
          # edgesToCreate dictionary once a nodeID has been found.
          if nodeID == None:
            absentNodeValues.append((task, argument))
            isNodeSet = False

          # If this is the node to use, remove the node from optionsToMerge and break out of the loop.
          if isNodeSet:
            optionsToMerge.pop(counter)
            break

        # If none of the nodes exist, a node needs to be created.  For now, store the edges that
        # need to be created
        if nodeID == None:
          tempNodeID                 = 'CREATE_NODE_' + str(missingNodeID)
          edgesToCreate[tempNodeID]  = []
          self.nodeIDs[configNodeID] = tempNodeID
          missingNodeID += 1
          for task, argument in absentNodeValues: edgesToCreate[tempNodeID].append((None, task, argument))

        # Mark the remaining nodes for deletion and also the nodeID of the node which this node is
        # to be merged with.
        else:

          # Initialise the dictionary if this nodeID is not present.
          if nodeID not in edgesToCreate: edgesToCreate[nodeID] = []

          # Initialise the entry for this nodeID and add any edges that are stored in the absentNodeValues list.
          for task, argument in absentNodeValues: edgesToCreate[nodeID].append((None, task, argument))

          # Now parse through the nodes remaining in the optionsToMerge structure and mark nodes and store edge
          # information. Check if any of the nodes being removed are filename stubs. If so, the node being kept
          # should be listed as a filenamd stub.
          mergedNodeIsFilenameStub = self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'isFilenameStub')
          for task, argument in optionsToMerge:
            nodeIDToRemove = self.nodeMethods.doesNodeExist(graph, task, argument)

            # Store the task and arguments for those nodes that are to be deleted or do not exist.
            # These will be needed to build edges to the merged nodes.
            edgesToCreate[nodeID].append((nodeIDToRemove, task, argument))

            # Only mark nodes that exist.
            if nodeIDToRemove != None: self.nodeMethods.setGraphNodeAttribute(graph, nodeIDToRemove, 'isMarkedForRemoval', True)

    return edgesToCreate

  # Create missing merged nodes.
  def createMissingMergedNodes(self, graph, edgesToCreate):
    createdNodes = {}
    for mergeNodeID in edgesToCreate:
      for nodeID, task, argument in edgesToCreate[mergeNodeID]:

        # If the node does not exist (i.e. none of the nodes being merged had been added to the graph),
        # the ID will begin with 'CREATE_NODE'.  If this is the case, create the node.
        if mergeNodeID.startswith('CREATE_NODE') and mergeNodeID not in createdNodes:
          tempNodeID = 'OPTION_' + str(self.nodeMethods.optionNodeID)
          self.nodeMethods.optionNodeID += 1
          tool       = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
          attributes = self.nodeMethods.buildNodeFromToolConfiguration(self.tools, tool, argument)
          graph.add_node(tempNodeID, attributes = attributes)

          # With the node addded, add the mergeNodeID to the dictionary containing nodes created in
          # this routine.  There will be at least two edges required for any of the nodes to be
          # created, but the node only needs to be created once.
          createdNodes[mergeNodeID] = tempNodeID

          # Modify the value in the nodeIDs dictionary to reflect this modified node value.
          for storedNodeID in self.nodeIDs:
            if mergeNodeID == self.nodeIDs[storedNodeID]: self.nodeIDs[storedNodeID] = tempNodeID

    # Having created all of the necessary nodes, update the edgesToCreate structure to include the new
    # IDs.
    for nodeID in createdNodes: edgesToCreate[createdNodes[nodeID]] = edgesToCreate.pop(nodeID)
 
  # For each node that is removed in the merging process, edges need to be created from the merged node
  # to the task whose original node has been merged.
  def createEdgesForMergedNodes(self, graph, edgesToCreate):
    for mergeNodeID in edgesToCreate:
      for nodeID, task, argument in edgesToCreate[mergeNodeID]:
        tool = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')

        if argument == 'read json file': self.edgeMethods.addJsonEdge(graph, mergeNodeID, task)

        # Add an edge from the merged node to this task.
        else: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, mergeNodeID, task, argument)

  # Create edges from the merged file nodes to the tasks whose own file nodes were marked
  # for removal in the merging process.  Filename stubs have to be handled here.
  def createEdgesForMergedFileNodes(self, graph, edgesToCreate):
    for mergeNodeID in edgesToCreate:
      for nodeID, task, argument in edgesToCreate[mergeNodeID]:

        # If the nodeID exists, then an option node for this task already exists in the graph and
        # has been marked for removal.  The associated file nodes will therefore, also exist and
        # so these should also be marked for removal.
        if nodeID:
          for fileNodeID in self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'associatedFileNodes'):
            self.nodeMethods.setGraphNodeAttribute(graph, fileNodeID, 'isMarkedForRemoval', True)

        # Only look at options nodes that contain information about files.
        if self.nodeMethods.getGraphNodeAttribute(graph, mergeNodeID, 'isFile'):
          tool                      = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
          mergedNodeIsFilenameStub  = self.nodeMethods.getGraphNodeAttribute(graph, mergeNodeID, 'isFilenameStub')

          # If the argument is 'read json file', create the edge.
          if argument == 'read json file':
            sourceNodeID = self.nodeMethods.getAssociatedFileNodeIDs(graph, mergeNodeID)[0]
            self.edgeMethods.addJsonEdge(graph, sourceNodeID, task)

          # Deal with actual tool arguments.
          else:
            removedNodeisFilenameStub = self.tools.getArgumentAttribute(tool, argument, 'isFilenameStub')
            if removedNodeisFilenameStub == None: removedNodeisFilenameStub = False
  
            # Find the short and long form of the argument.
            longFormArgument     = self.tools.getLongFormArgument(tool, argument)
            shortFormArgument    = self.tools.getArgumentAttribute(tool, longFormArgument, 'shortFormArgument')
            isInput              = self.tools.getArgumentAttribute(tool, longFormArgument, 'isInput')
            isOutput             = self.tools.getArgumentAttribute(tool, longFormArgument, 'isOutput')
  
            # If the argument is not for a filename stub, then there is a single output file.
            # Generate the edges from the replacement file value to this task.
            if not mergedNodeIsFilenameStub and not removedNodeisFilenameStub:
              self.linkNonFilenameStubNodes(graph, mergeNodeID, nodeID, task, shortFormArgument, longFormArgument, isInput)
  
            # If either of the nodes are filename stubs, deal with them.
            elif mergedNodeIsFilenameStub and not removedNodeisFilenameStub:
              self.createFilenameStubEdgesM(graph, mergeNodeID, nodeID, task, shortFormArgument, longFormArgument)
            elif not mergedNodeIsFilenameStub and removedNodeisFilenameStub:
              self.createFilenameStubEdgesR(graph, mergeNodeID, nodeID, task, tool, shortFormArgument, longFormArgument)
            elif mergedNodeIsFilenameStub and removedNodeisFilenameStub:
              self.createFilenameStubEdgesMR(graph, mergeNodeID, nodeID, task, shortFormArgument, longFormArgument, isInput)

  # Create the edges for file nodes that are not generated from filename stubs.
  def linkNonFilenameStubNodes(self, graph, mergeNodeID, nodeID, task, shortFormArgument, longFormArgument, isInput):

    # Find the file nodes associated with the option node.
    mergeFileNodeIDs = self.nodeMethods.getAssociatedFileNodeIDs(graph, mergeNodeID)

    # If the node has been created, find the associated file node IDs. If the node has not yet been 
    # created in the graph, this is unnecessary.
    if nodeID != None:
      fileNodeIDs = self.nodeMethods.getAssociatedFileNodeIDs(graph, nodeID)

      if len(mergeFileNodeIDs) != 1 or len(fileNodeIDs) != 1:
        #TODO SORT ERROR.
        print('UNEXPECTED NUMBER OF FILENODE IDs - createEdgesForMergedFileNodes')
        self.errors.terminate()

    tool = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
    if isInput: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, mergeFileNodeIDs[0], task, longFormArgument)
    else: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, task, mergeFileNodeIDs[0], longFormArgument)

  # Create the edges for file nodes that are generated from filename stubs.  Specifically, deal
  # with the case where the node being kept is a filename stub and the node being removed is not.
  def createFilenameStubEdgesM(self, graph, mergeNodeID, nodeID, task, shortFormArgument, longFormArgument):
    foundMatch = False
    tool       = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')

    # The node being kept is a filename stub, so has multiple file nodes associated with it. First
    # loop over these file nodes.
    for fileNodeID in self.nodeMethods.getAssociatedFileNodeIDs(graph, mergeNodeID):
      
      # Get the extension that the file is expecting. Add a '.' to the front of this extension. The extensions
      # supplied for filename stubs begin with a '.'.
      extensionA = self.pipeline.getExtension(task, longFormArgument, self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'linkedExtension'))
      extensionB = '.' + self.pipeline.getExtension(task, longFormArgument, self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'linkedExtension'))
      allowedExtensions = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'allowedExtensions')
      if extensionA in allowedExtensions or extensionB in allowedExtensions:
        foundMatch = True

        # Create the edge from the file node to the task.
        isInput = self.tools.getArgumentAttribute(tool, longFormArgument, 'isInput')
        if isInput: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, fileNodeID, task, longFormArgument)
        else: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, task, fileNodeID, longFormArgument)
        break

    # If the expected extension was not available in any of the file nodes, this must be an error in the
    # pipeline configuration file.
    if not foundMatch:
      #TODO ERROR
      print('createFilenameStubEdgesM')
      self.errors.terminate()

  # Create the edges for file nodes that are generated from filename stubs.  Specifically, deal
  # with the case where the node being kept is not a filename stub and the node being removed is.
  def createFilenameStubEdgesR(self, graph, mergeNodeID, nodeID, task, tool, shortFormArgument, longFormArgument):
    fileNodeIDs = []

    # Since the node being kept is not a filename stub, it will only have a single file node associated
    # with it. Rename this file node (with the suffix '_1') and create the correct number of file nodes.
    mergeFileNodeIDs = self.nodeMethods.getGraphNodeAttribute(graph, mergeNodeID, 'associatedFileNodes')
    if len(mergeFileNodeIDs) != 1:
      #TODO SORT ERROR.
      print('UNEXPECTED NUMBER OF FILENODE IDs - createFilenameStubEdgesR')
      self.errors.terminate()

    # Get the extensions for the output files.
    outputExtensions = self.tools.getArgumentAttribute(tool, longFormArgument, 'filenameExtensions')

    # Rename the existing file node and reset the extension.
    self.nodeMethods.renameNode(graph, self.tools, mergeFileNodeIDs[0], mergeFileNodeIDs[0] + '_1', allowNullArgument = False)
    fileNodeIDs.append(mergeFileNodeIDs[0] + '_1')

    # Update the attributes for the renamed file node.
    self.nodeMethods.setGraphNodeAttribute(graph, mergeFileNodeIDs[0] + '_1', 'allowedExtensions', [outputExtensions[0]], True)

    # Create the additional file nodes.
    for count in range(2, len(outputExtensions) + 1):
      extension                      = outputExtensions[count - 1]
      fileNodeID                     = mergeNodeID + '_FILE_' + str(count)
      attributes                     = fileNodeAttributes()
      attributes.description         = self.nodeMethods.getGraphNodeAttribute(graph, mergeNodeID, 'description')
      attributes.allowMultipleValues = self.nodeMethods.getGraphNodeAttribute(graph, mergeNodeID, 'allowMultipleValues')
      attributes.allowedExtensions   = [extension]
      fileNodeIDs.append(fileNodeID)
      graph.add_node(fileNodeID, attributes = attributes)

    # Create edges from all of the file nodes to the task associated with the node being removed.
    for fileNodeID in fileNodeIDs:
      isInput = self.tools.getArgumentAttribute(tool, longFormArgument, 'isInput')
      if isInput: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, fileNodeID, task, longFormArgument)
      else: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, task, fileNodeID, longFormArgument)

    #TODO WHAT IF WE WANT ALL OF THE OUTPUTS FROM THE TOOL WITH THE FILENAME STUB TO GO TO THE NEXT NODE?

  # Create the edges for file nodes that are generated from filename stubs.  Specifically, deal
  # with the case where both the node being kept and the node being removed are filename stubs.
  # This is the simplest case, as no choices are required, just include edges from all the file
  # nodes to the task.
  def createFilenameStubEdgesMR(self, graph, mergeNodeID, nodeID, task, shortFormArgument, longFormArgument, isInput):
    mergeFileNodeIDs = self.nodeMethods.getGraphNodeAttribute(graph, mergeNodeID, 'associatedFileNodes')
    for mergeFileNodeID in mergeFileNodeIDs:
      tool = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
      if isInput: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, mergeFileNodeID, task, longFormArgument)
      else: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, task, mergeFileNodeID, longFormArgument)

  # Parse through all of the nodes that have been merged and check if they have files that
  # were marked in the pipeline configuration file as files that should be deleted. If such
  # nodes exist, mark them.
  def markNodesWithFilesToBeKept(self, graph):
    for configNodeID in self.nodeIDs:
      nodeID = self.nodeIDs[configNodeID]
      if self.pipeline.getNodeAttribute(configNodeID, 'deleteFiles'): self.nodeMethods.setGraphNodeAttribute(graph, nodeID, 'deleteFiles', True)

  # Some file nodes were created for filename stubs. In the merge process, some nodes are renamed that are
  # connected to subsequent tasks. Consider a task that produces files NODE1 and NODE2 from a single 
  # filename stub argument. Each of these files is the processed by tasks A and B. Prior to the merge, a
  # single file node existed, NODE. This was renamed to NODE1 and NODE2 was created. Since tasks A and B
  # already had edges, both tasks use NODE1 as input, which is incorrect. In this case, task B needs an
  # edge from NODE2 and the edge from NODE1 needs to be deleted.
  #
  # Search for all arguments in the pipeline configuration file that define tasks that link to specified
  # extensions and check that the nodes are consistent.
  def checkEdges(self, graph):

    # Loop over all nodes in the pipeline configuration file.
    for configNodeID in self.pipeline.commonNodes:

      # Only look at configuration file nodes that specify that argument point to a file extension (i.e.
      # a filename stub produced multiple files with multiple extensions).
      if configNodeID in self.pipeline.linkedExtension:

        # Loop over all the tasks that are associated with this pipeline configuration file node.
        for task, taskArgument in self.pipeline.commonNodes[configNodeID]:

          # Only proceed if this task/argument pair are defined as connecting to one of these files.
          if task in self.pipeline.linkedExtension[configNodeID]:
            if taskArgument in self.pipeline.linkedExtension[configNodeID][task]:

              # Get the expected extension for this task/argument pair.
              extension = self.pipeline.linkedExtension[configNodeID][task][taskArgument]

              # Loop over all option nodes for this task.
              for optionNodeID in self.nodeMethods.getNodeForTaskArgument(graph, task, taskArgument, 'option'):
                fileNodeIDs   = self.nodeMethods.getAssociatedFileNodeIDs(graph, optionNodeID)
                isInput       = self.edgeMethods.getEdgeAttribute(graph, optionNodeID, task, 'isInput')

                # Loop over this tasks file nodes.
                for fileNodeID in fileNodeIDs:

                  # Check if an edge exists between this file node and the task (input or output file).
                  if isInput: edgeExists = self.edgeMethods.checkIfEdgeExists(graph, fileNodeID, task)
                  else: edgeExists = self.edgeMethods.checkIfEdgeExists(graph, task, fileNodeID)

                  # If an edge exists, check if the extension associated with the file node matches that
                  # defined in the pipeline configuration file.
                  if edgeExists:

                    # If the extensions don't match, delete the existing edge and determine the node to which the
                    # edge should be attached and create this edge.
                    if extension != self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'allowedExtensions')[0]:

                      # Search for the file node with the correct extension.
                      foundCorrectNode = False
                      for correctFileNodeID in fileNodeIDs:
                        if extension == self.nodeMethods.getGraphNodeAttribute(graph, correctFileNodeID, 'allowedExtensions')[0]:
                          foundCorrectNode = True
                          break

                      # If there is no node with the expected extension, fail.
                      if not foundCorrectNode: self.errors.noFileNodeIDWithCorrectExtension(task, taskArgument, extension)

                      # Update the edges.
                      if isInput:
                        graph.add_edge(correctFileNodeID, task, attributes = graph[fileNodeID][task]['attributes'])
                        graph.remove_edge(fileNodeID, task)
                      else:
                        graph.add_edge(task, correctFileNodeID, attributes = graph[task][fileNodeID]['attributes'])
                        graph.remove_edge(task, fileNodeID)

  # Mark all greedy edges in the graph.
  def markGreedyEdges(self, graph):

    # Loop over all of the tasks.
    for task in self.pipeline.greedyTasks:
      hasGreedyArgument = False
      for argument in self.pipeline.greedyTasks[task]:
        for sourceNodeID in self.nodeMethods.getNodeForTaskArgument(graph, task, argument, 'option'):
          self.edgeMethods.setEdgeAttribute(graph, sourceNodeID, task, 'isGreedy', True)
          hasGreedyArgument = True

      # If any of the arguments for this task were marked as greedy, mark the task as greedy.
      if hasGreedyArgument: self.nodeMethods.setGraphNodeAttribute(graph, task, 'isGreedy', True)

  # Check to see if there are any isolated nodes in the pipeline.
  def checkForIsolatedNodes(self, graph):
    isolatedNodes = []
    isIsolated    = False

    for task in self.pipeline.workflow:
      isTaskIsolated = True

      # Check if any of the files being used as input are used by any other tasks in the pipeline.
      for fileNodeID in self.nodeMethods.getPredecessorFileNodes(graph, task):

        # Check if the file is used by any other tasks,
        if graph.predecessors(fileNodeID) or len(graph.successors(fileNodeID)) > 1: isTaskIsolated = False

      # Now check if any files output by the task are used by other tasks.
      for fileNodeID in self.nodeMethods.getSuccessorFileNodes(graph, task):
        if graph.successors(fileNodeID): isTaskIsolated = False

      if isTaskIsolated:
        isolatedNodes.append(task)
        isIsolated = True

    return isIsolated, isolatedNodes

  # Generate the task workflow from the topologically sorted pipeline graph.
  def generateWorkflow(self, graph):
    workflow = []
    for nodeID in nx.topological_sort(graph):
      if self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'nodeType') == 'task': workflow.append(nodeID)

    return workflow

  # The topological sort does not always generate the corret order. In this routine, check for
  # cases where a task is outputting to the stream. It is possible that after this task, the
  # topological sort could choose from multiple tasks to perform next. This routine exists to
  # ensure that the task following is the one that expects the stream as its input.
  def correctWorkflowForStreams(self, graph, workflow):
    incomplete = True
    startCount = 0
    while incomplete:
      for counter in range(startCount, len(workflow)):
        task = workflow[counter]
        if counter == len(workflow) - 1: incomplete = False
        else:
          nextTask         = workflow[counter + 1]
          isOutputToStream = self.nodeMethods.getGraphNodeAttribute(graph, task, 'outputStream')
  
          # If this task outputs to the stream, determine what the next task should be.
          if isOutputToStream:
            for outputNodeID in graph.successors(task):
              successorTasks  = []
              for successorTask in graph.successors(outputNodeID): successorTasks.append(successorTask)
  
            # If the next task is not in the list of tasks, modify the workflow to ensure that it is.
            successorIndex = workflow.index(successorTasks[0])
            if nextTask not in successorTasks:

              # Store the tasks 
              workflowMiddle = []
              workflowMiddle = workflow[counter + 1: successorIndex]

              # Find all the tasks after the successor task. Once tasks have been moved around, these
              # tasks will all be added to the end.
              workflowEnd = []
              workflowEnd = workflow[successorIndex + 1:]
  
              # Reconstruct the workflow.
              updatedWorkflow = []
              for updateCount in range(0, counter + 1): updatedWorkflow.append(workflow[updateCount])
              updatedWorkflow.append(successorTasks[0])
              for updateTask in workflowMiddle: updatedWorkflow.append(updateTask)
              for updateTask in workflowEnd: updatedWorkflow.append(updateTask)
 
              # Update the workflow.
              workflow = updatedWorkflow

              # Reset the startCount. There is no need to loop over the entire workflow on the next
              # pass.
              startCount = counter
              break

    return workflow

  # Process any 'additional nodes' for this pipeline.
  def processAdditionalNodes(self, graph):
    if self.pipeline.hasAdditionalNodes:
      for configNodeID in self.pipeline.additionalNodes:

        # Get the ID of a new node to be created.
        nodeID = 'OPTION_' + str(self.nodeMethods.optionNodeID)
        self.nodeMethods.optionNodeID += 1

        # Use the first task in the list to generate the option node details.
        task       = self.pipeline.additionalNodes[configNodeID].keys()[0]
        tool       = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
        argument   = self.pipeline.additionalNodes[configNodeID][task][0]
        attributes = self.nodeMethods.buildNodeFromToolConfiguration(self.tools, tool, argument)
        isInput    = self.tools.getArgumentAttribute(tool, argument, 'isInput')
        isOutput   = self.tools.getArgumentAttribute(tool, argument, 'isOutput')
        isFile = True if (isInput or isOutput) else False
        graph.add_node(nodeID, attributes = attributes)

        # If this argument points to a file, also create a file node.
        if isFile:
          isFilenameStub = self.tools.getArgumentAttribute(tool, argument, 'isFilenameStub')

          # Deal with filename stubs.
          if isFilenameStub: print('config.processAdditionalNodes - Not handled stubs'); exit(0)

          # Deal with non-filename stubs.
          else:
            attributes                     = fileNodeAttributes()
            fileNodeID                     = nodeID + '_FILE'
            attributes.description         = self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'description')
            attributes.allowMultipleValues = self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'allowMultipleValues')
            attributes.allowedExtensions   = self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'allowedExtensions')
            graph.add_node(fileNodeID, attributes = attributes)

        # Loop over all the task/argument pairs and add edges to the new node.
        for task in self.pipeline.additionalNodes[configNodeID]:
          for argument in self.pipeline.additionalNodes[configNodeID][task]:
            self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, nodeID, task, argument, False)
            if isFile:
              tool    = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
              isInput = self.tools.getArgumentAttribute(tool, argument, 'isInput')
              if not isFilenameStub:
                if isInput: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, fileNodeID, task, argument, False)
                else: self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, task, fileNodeID, argument, False)
          
  # Having added the 'additional nodes' to the graph, attach unassigned pipeline arguments.
  def connectPipelineArgumentsFromAdditionalNodes(self, graph):

    # Loop over all the pipeline arguments that are unassigned.
    for configNodeID, pipelineArgument in self.pipeline.unassignedArguments:

      # If the pipeline configuration node does not have additional nodes, this argument cannot be
      # connected to a node since no tool arguments are associated with it.
      #TODO ERROR
      if configNodeID not in self.pipeline.additionalNodes: print('ERROR: config.connectPipelineArgumentsFromAdditionalNodes'); exit(0)

      # Get the first task/tool argument pair from the additional nodes.
      task         = self.pipeline.additionalNodes[configNodeID].keys()[0]
      tool         = self.pipeline.taskAttributes[task].tool
      taskArgument = self.pipeline.additionalNodes[configNodeID][task][0]
      foundArgument  = False
      for predecessorNodeID in self.nodeMethods.getPredecessorOptionNodes(graph, task):
        testArgument = self.edgeMethods.getEdgeAttribute(graph, predecessorNodeID, task, 'longFormArgument')
        if taskArgument == testArgument:
          foundArgument = True
          break

      # Set the nodeID to a graph nodeID.
      self.pipeline.pipelineArguments[pipelineArgument].ID = predecessorNodeID

  # Attach the parameter set arguments to the relevant nodes.
  def attachPipelineParameterSetArgumentsToNodes(self, graph, pipelineName, parameterSetName):
    for node in self.parameterSets.parameterSetAttributes[pipelineName][parameterSetName].nodes:

      # Get the long form of the argument.
      longFormArgument, shortFormArgument = self.pipeline.getLongFormArgument(graph, node.argument)
      configNodeID                        = self.pipeline.pipelineArguments[longFormArgument].configNodeID

      # Check all of the available pipeline arguments and find the node ID of the node which
      # this parameter set node is trying to set.
      try: nodeIDToSet = self.nodeIDs[configNodeID]

      # If the configuration node ID is not present in the nodeIDs data structure, it may be
      # because no graph nodes were merged (the nodeIDs data structure is built up when merging
      # graph nodes). In this case, take one of the task/argument pairs associated with this
      # configuration file node and determine the graph node to which this points.
      except:
        task, argument = self.pipeline.commonNodes[configNodeID][0]
        try: nodeIDToSet = self.nodeMethods.getNodeForTaskArgument(graph, task, argument, 'option')[0]
        except:
          #TODO ERROR
          print('WOOPS - configurationData.attachPipelineParameterSetArgumentsToNodes')
          self.errors.terminate()

      # All of the values extracted from the parameter set json file are unicode.  Convert them to strings.
      for counter, value in enumerate(node.values): node.values[counter] = str(value)
      self.nodeMethods.addValuesToGraphNode(graph, nodeIDToSet, node.values, write = 'replace')

  # Attach the paramater set arguments to the relevant nodes.
  def attachToolParameterSetArgumentsToNodes(self, graph, task, parameterSet):

    # If this is a pipeline adding the tool parameter set values, ensure that the task and tool are identified.
    if self.isPipeline: tool = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
    else: tool = task

    for node in self.parameterSets.parameterSetAttributes[tool][parameterSet].nodes:
      nodeIDToSet = None

      # Check all of the nodes set for this tool, determine the associated arguments and find the node ID
      # of the node for the argument set in the parameter set.
      for nodeID in self.nodeMethods.getPredecessorOptionNodes(graph, task):
        argument = self.edgeMethods.getEdgeAttribute(graph, nodeID, task, 'longFormArgument')
        if argument == node.argument: nodeIDToSet = nodeID

      # If the node doesn't exist, check that the argument requested in the parameter set is valid for this tool.
      if not nodeIDToSet:
        if node.argument not in self.tools.longFormArguments[tool] and node.argument not in self.tools.shortFormArguments[tool]:
          self.errors.invalidArgumentInToolParameterSet(tool, parameterSet, node.ID, node.argument)

        # Get the long form of the argument to be set.
        longFormArgument  = self.tools.getLongFormArgument(tool, node.argument)

        # Define the node attributes using information from the tool configuration file.
        attributes = self.nodeMethods.buildNodeFromToolConfiguration(self.tools, tool, longFormArgument)

        # Create a new node for the argument.
        nodeIDToSet = 'OPTION_' + str(self.nodeMethods.optionNodeID)
        self.nodeMethods.optionNodeID += 1
        graph.add_node(nodeIDToSet, attributes = attributes)

        # Add an edge from the new node to the tool node.
        self.edgeMethods.addEdge(graph, self.nodeMethods, self.tools, nodeIDToSet, task, longFormArgument)

        # If the option node corresponds to a file, build a file node.
        if self.nodeMethods.getGraphNodeAttribute(graph, nodeIDToSet, 'isFile'):
          shortFormArgument = self.edgeMethods.getEdgeAttribute(graph, nodeIDToSet, tool, 'shortFormArgument')
          if self.nodeMethods.getGraphNodeAttribute(graph, nodeIDToSet, 'isInput'):
            self.nodeMethods.buildTaskFileNodes(graph, self.tools, nodeIDToSet, tool, longFormArgument, shortFormArgument, 'input')
          else:
            self.nodeMethods.buildTaskFileNodes(graph, self.tools, nodeIDToSet, tool, longFormArgument, shortFormArgument, 'input')

      # Add the values to the node.
      self.nodeMethods.addValuesToGraphNode(graph, nodeIDToSet, node.values, write = 'replace')

  # Check that all required files and values have been set. All files and parameters that are listed as
  # required by the individual tools should already have been checked, but if the pipeline has some
  # additional requirements, these may not yet have been checked.
  def checkRequiredFiles(self, graph, gknoConfig):

    # Loop over all of the tasks in the pipeline.
    for task in self.pipeline.workflow:

      # Loop over all predecessor file nodes.
      for fileNodeID in self.nodeMethods.getPredecessorFileNodes(graph, task):
        optionNodeID = self.nodeMethods.getOptionNodeIDFromFileNodeID(fileNodeID)
        isRequired   = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'isRequired')
        if not self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'values') and isRequired:

          # Get the long and short form of the argument.
          taskLongFormArgument  = self.edgeMethods.getEdgeAttribute(graph, fileNodeID, task, 'longFormArgument')
          taskShortFormArgument = self.edgeMethods.getEdgeAttribute(graph, fileNodeID, task, 'shortFormArgument')

          # CHeck to see if this argument can be constructed.
          tool                                  = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
          cLongFormArgument, cShortFormArgument = gknoConfig.construct.canToolArgumentBeConstructed(graph, self, tool, taskLongFormArgument)
          if cLongFormArgument:
            taskLongFormArgument  = cLongFormArgument
            taskShortFormArgument = cShortFormArgument

          # Check if there are pipeline arguments for the missing argument.
          pipelineLongFormArgument, pipelineShortFormArgument = self.pipeline.getPipelineArgument(task, taskLongFormArgument)

          # Check for other nodes using that use this argument. If edges were defined in the configuration file, it
          # is possible that the task has all of the required arguments, even though this node is empty.
          isAlternativeSet = False
          for testNodeID in self.nodeMethods.getPredecessorFileNodes(graph, task):
            testArgument = self.edgeMethods.getEdgeAttribute(graph, testNodeID, task, 'longFormArgument')

            # If this test node is not the current node, uses the same argument and has values, terminate the
            # loop since an alternative argument exists and is set.
            if (testNodeID != fileNodeID) and (testArgument == taskLongFormArgument):
              if self.nodeMethods.getGraphNodeAttribute(graph, testNodeID, 'values'):
                isAlternativeSet = True
                break

          # If no values are set for this argument, terminate.
          if not isAlternativeSet:
            if not self.isPipeline or not pipelineLongFormArgument:
              description       = self.tools.getArgumentAttribute(tool, taskLongFormArgument, 'description')
              validAlternatives = self.tools.getArgumentAttribute(tool, taskLongFormArgument, 'canBeSetByArgument')
              alternatives      = []
              for alternative in validAlternatives: alternatives.append((alternative, self.tools.longFormArguments[tool][alternative]))
              self.errors.unsetFile(taskLongFormArgument, taskShortFormArgument, description, alternatives, self.isPipeline)
            else:
              description       = self.pipeline.pipelineArguments[pipelineLongFormArgument].description
              validAlternatives = self.tools.getArgumentAttribute(tool, taskLongFormArgument, 'canBeSetByArgument')
              alternatives      = []
              for alternative in validAlternatives:
                pipelineLongFormAlternative, pipelineShortFormAlternative = self.pipeline.getPipelineArgument(task, alternative)
                alternatives.append((pipelineLongFormAlternative, pipelineShortFormAlternative))
              self.errors.unsetFile(pipelineLongFormArgument, pipelineShortFormArgument, description, alternatives, False)

          # If the node has no values, but alternative nodes for this argument exist and have values, remove these
          # empty nodes from the graph.
          else:
            self.nodeMethods.setGraphNodeAttribute(graph, optionNodeID, 'isMarkedForRemoval', True)
            self.nodeMethods.setGraphNodeAttribute(graph, fileNodeID, 'isMarkedForRemoval', True)

    # Purge all nodes that have been marked for removal.
    self.nodeMethods.purgeNodeMarkedForRemoval(graph)

  # Determine all of the graph dependencies.  This is essentially
  def getGraphDependencies(self, graph, taskList, key):
    dependencies = []
    for task in taskList:
      for fileNodeID in self.nodeMethods.getPredecessorFileNodes(graph, task):

        # If the node refers to a directory, do not include it.
        optionNodeID = self.nodeMethods.getOptionNodeIDFromFileNodeID(fileNodeID)
        #if not self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'isDirectory'):

        # Determine if the file node has any predecessors.
        hasPredecessor = self.nodeMethods.hasPredecessor(graph, fileNodeID)

        # If there are no predecessors, find the name of the file and add to the list of dependencies.
        # Since the values associated with a node is a dictionary of lists, if 'key' is set to 'all',
        # get all of the values, otherwise, just get those with the specified key.
        if not hasPredecessor:
          values = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'values')
          if values:
            if key == 'all':
              for iteration in values.keys():
                for value in values[iteration]: dependencies.append((fileNodeID, value))
  
            # Just get values for a particular key.
            elif key in values:
              for value in values[key]: dependencies.append((fileNodeID, value))
  
            # TODO CHECK
            elif key not in values and key != 1:
               for value in values[1]: dependencies.append((fileNodeID, value))
  
            # If the key is unknown, fail.
            #TODO Errors.
            else:
              print('UNKNOWN KEY: configurationClass.getGraphDependencies', key)
              print(values)
              self.errors.terminate()

    return dependencies

  # Determine all of the outputs.  This is essentially all file nodes with no predecessors.
  def getGraphOutputs(self, graph, taskList, deleteList, key):

    # Collect all the files that are deleted in this phase.
    filesDeleted = []
    for task in deleteList:
      for iteration in deleteList[task]:
        for filename in deleteList[task][iteration]: filesDeleted.append(filename)

    outputs     = []
    for task in taskList:
      for fileNodeID in self.nodeMethods.getSuccessorFileNodes(graph, task):
        optionNodeID = self.nodeMethods.getOptionNodeIDFromFileNodeID(fileNodeID)
        deleteFiles  = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'deleteFiles')
        isStreaming  = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'isStreaming')
        isTemporary  = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'isTemporary')
        values       = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'values')

        # Get the tasks associated with this option node.
        tasks = graph.successors(optionNodeID)

        # By default, all files produced by the pipeline are kept and so should be listed as
        # outputs. However, some files are listed as to be deleted, so do not include these as
        # outputs.
        #if not deleteFiles and not isStreaming and not isTemporary:
        if not isStreaming and not isTemporary:
          if key == 'all':
            for iteration in values.keys():
              for value in values[iteration]:
                if value not in filesDeleted: outputs.append((optionNodeID, value))
  
          # Just get values for a particular key.
          elif key in values:
            for value in values[key]:
              if value not in filesDeleted: outputs.append((optionNodeID, value))
  
          #TODO CHECK
          elif key not in values and key != 1:
            for value in values[1]:
              if value not in filesDeleted: outputs.append((optionNodeID, value))

          # If the key is unknown, fail.
          #TODO Errors.
          else:
            print('UNKNOWN KEY: configurationClass.getGraphOutputs', key)
            self.errors.terminate()

    return outputs

  # Determine all of the intermediate files in the graph.  This is all of the file nodes that have both
  # predecessor and successor nodes.
  def getGraphIntermediateFiles(self, graph, taskList):
    intermediates = {}
    seenNodes     = {}
    for task in taskList:
      for fileNodeID in self.nodeMethods.getPredecessorFileNodes(graph, task):

        # If the node refers to a directory, do not include it.
        optionNodeID = self.nodeMethods.getOptionNodeIDFromFileNodeID(fileNodeID)
        if not self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'isDirectory'):
          deleteFiles  = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'deleteFiles')
  
          # Determine if the node has any predecessors or successors.
          hasPredecessor = self.nodeMethods.hasPredecessor(graph, fileNodeID)
          hasSuccessor   = self.nodeMethods.hasSuccessor(graph, fileNodeID)
  
          # Store this node in the list of nodes that have been checked. This ensures that the same nodes
          # values aren't added to the list multiple times. For example, if a file is produced by one task
          # and is then used by multiple subsequent tasks, the same node will come up for each of the tasks
          # that use the file, but it should only be listed as an intermediate file once.
          if fileNodeID not in seenNodes:
            seenNodes[fileNodeID] = True
            if hasPredecessor and hasSuccessor and deleteFiles:
              values = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'values')
  
              # Do not include streaming nodes.
              if not self.edgeMethods.getEdgeAttribute(graph, fileNodeID, task, 'isStreaming'):
                for iteration in values.keys():
                  if iteration not in intermediates: intermediates[iteration] = []
                  for value in values[iteration]: intermediates[iteration].append((optionNodeID, value))
      
    return intermediates

  # Deterrmine when each intermediate file is last used,
  def setWhenToDeleteFiles(self, graph, intermediates):
    deleteList = {}
    for counter in intermediates:
      for nodeID, filename in intermediates[counter]:
  
        # Find the successor task nodes.
        successorNodeIDs = graph.successors(nodeID)
  
        # Determine which of these tasks comes last in the workflow.
        for task in reversed(self.pipeline.workflow):
          if task in successorNodeIDs: break
  
        # Store the task when the file can be deleted.
        if filename in deleteList:
          # TODO ERROR
          print('SAME FILENAME', filename, 'appears multiple times in the list of intermediate files - setWhenToDeleteFiles')
          self.errors.terminate()
  
        if task not in deleteList: deleteList[task] = {}
        if counter not in deleteList[task]: deleteList[task][counter] = []
        deleteList[task][counter].append(filename)

    return deleteList

  # Get all of the outputs from a task.
  def getTaskOutputs(self, graph, task, iteration):
    outputIDs = []
    for fileNodeID in self.nodeMethods.getSuccessorFileNodes(graph, task):

      # If this node is a streaming node, no output file is produced, so do not include
      # it in the list. Also, do not include directories.
      optionNodeID = self.nodeMethods.getOptionNodeIDFromFileNodeID(fileNodeID)
      isDirectory  = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'isDirectory')
      isStreaming  = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'isStreaming')
      isTemporary  = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'isTemporary')
      if not isStreaming and not isTemporary:
      #if not isStreaming and not isDirectory:
        values = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'values')
        if iteration == 'all':
          for counter in values:
            for value in values[counter]: outputIDs.append(value)
  
        elif iteration in values:
          for value in values[iteration]: outputIDs.append(value)
  
        elif iteration != 1:
          for value in values[1]: outputIDs.append(value)
  
        else:
          #TODO ERROR
          print('Unknown iteration in getTaskOutputs.')
          self.errors.terminate()

    return outputIDs

  # Get all of the dependencies for a task.
  def getTaskDependencies(self, graph, task, isGreedy, iteration):
    dependencies = []
    for fileNodeID in self.nodeMethods.getPredecessorFileNodes(graph, task):

      # If this node is a streaming node, no output file is produced, so do not include
      # it in the lise.
      optionNodeID = self.nodeMethods.getOptionNodeIDFromFileNodeID(fileNodeID)
      isDirectory  = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'isDirectory')
      isStreaming  = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'isStreaming')
      #if not isStreaming and not isDirectory:
      if not isStreaming:
        values = self.nodeMethods.getGraphNodeAttribute(graph, fileNodeID, 'values')
        if values:

          # If the task is greedy, use all of the iterations.
          if isGreedy: iteration = 'all'
  
          # Get the dependencies.
          if iteration == 'all':
            for counter in values:
              for value in values[counter]: dependencies.append(value)
    
          elif iteration in values:
            for value in values[iteration]: dependencies.append(value)
    
          elif iteration != 1:
            for value in values[1]: dependencies.append(value)
    
          else:
            #TODO ERROR
            print('Unknown iteration in getTaskDependencies.')
            self.errors.terminate()

    return dependencies

  # For each task, determine the maximum number of datasets associated with any option.
  def getNumberOfDataSets(self, graph):
    for task in self.pipeline.workflow:
      totalNumber                  = 0
      isGreedy                     = False
      hasMultipleInputFiles        = False
      hasMultipleNonFileParameters = False
      for nodeID in self.nodeMethods.getPredecessorOptionNodes(graph, task):
        numberOfDataSets = len(self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'values'))
        isInput          = self.edgeMethods.getEdgeAttribute(graph, nodeID, task, 'isInput')
        isFile           = self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'isFile')

        # Record if this task has multiple input files.
        if isInput and numberOfDataSets > 1: hasMultipleInputFiles = True

        # Record if this task has multiple iterations of a non filename parameter.
        if not isFile and numberOfDataSets > 1: hasMultipleNonFileParameters = True

        # Update the number of data sets.
        if numberOfDataSets > totalNumber: totalNumber = numberOfDataSets

        # Check if this option is greedy. If the task has a greedy argument, then the number
        # of data sets is one. This is only true if the input argument with multiple values is a 
        # file. For example, if there is a single input file and multiple parameters, there will
        # be multiple output files, even though the task is greedy.
        if self.edgeMethods.getEdgeAttribute(graph, nodeID, task, 'isGreedy'): isGreedy = True

      #TODO Check the inclusion of hasMultipleNonFileParameters does not break things.
      # If the task is greedy, check which argument has multiple values. If it is a file, then
      # the number of data sets is one. If there are multiple
      if isGreedy and hasMultipleInputFiles and not hasMultipleNonFileParameters:
        self.nodeMethods.setGraphNodeAttribute(graph, task, 'numberOfDataSets', 1)
      else: self.nodeMethods.setGraphNodeAttribute(graph, task, 'numberOfDataSets', totalNumber)

  # Set commands to evaluate at run time.
  def evaluateCommands(self, graph):

    # Loop over all pipeline arguments with commands to evaluate at run time.
    for task in self.pipeline.evaluateCommands:
      tool = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
      for longFormArgument in self.pipeline.evaluateCommands[task]:

        # Check if values have been set for this argument. If not, set the values as the command
        # to execute.
        #TODO Is it possible for multiple nodes to exist in the following call?
        try: nodeID = self.nodeMethods.getNodeForTaskArgument(graph, task, longFormArgument, 'option')[0]
        except:
          #TODO ERROR
          print('\tconfigurationClass.evaluateCommands - no node IDs')
          self.errors.terminate()

        if not self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'values'):

          # Get all of the arguments that feed values to the command.
          commandValues    = {}
          numberOfDataSets = 0
          linkedNodeIDs    = []
          for ID in self.pipeline.evaluateCommands[task][longFormArgument].values:
            linkedTask     = self.pipeline.evaluateCommands[task][longFormArgument].values[ID][0]
            linkedTool     = self.nodeMethods.getGraphNodeAttribute(graph, linkedTask, 'tool')
            linkedArgument = self.pipeline.evaluateCommands[task][longFormArgument].values[ID][1]

            # Check that the argument is valid.
            linkedLongFormArgument = self.tools.getLongFormArgument(linkedTool, linkedArgument, False)
            if linkedLongFormArgument == None: self.errors.invalidArgumentInEvaluateCommand(ID, linkedTask, linkedArgument)
            linkedNodeID      = self.nodeMethods.getNodeForTaskArgument(graph, linkedTask, linkedLongFormArgument, 'option')[0]
            commandValues[ID] = self.nodeMethods.getGraphNodeAttribute(graph, linkedNodeID, 'values')
            numberOfDataSets  = len(commandValues[ID]) if len(commandValues[ID]) > numberOfDataSets else numberOfDataSets
            linkedNodeIDs.append(linkedNodeID)

          # Check that all of the values have the same number of data sets.
          for ID in commandValues: 
            if len(commandValues[ID]) == 0:
              #TODO ERROR
              print('configurationClass.evaluateCommands - number of values')
              self.errors.terminate()

            if len(commandValues[ID]) != 1 and len(commandValues[ID]) != numberOfDataSets:
              #TODO ERROR
              print('configurationClass.evaluateCommands - number of values')
              self.errors.terminate()

          # Build the command for each data set.
          commands = {}
          for count in range(1, numberOfDataSets + 1):
            commands[count] = self.pipeline.evaluateCommands[task][longFormArgument].command
            for ID in commandValues:
              if len(commandValues[ID]) == 1:
                if len(commandValues[ID][1]) != 1:
                  #TODO ERROR                               
                  print('configurationClass.evaluateCommands - number of values')
                  self.errors.terminate() 
                commands[count] = [str('$(') + str(commands[count].replace(str(ID), str(commandValues[ID][1][0]))) + str(')')]

              else:
                if len(commandValues[ID][count]) != 1:
                  #TODO ERROR                               
                  print('configurationClass.evaluateCommands - number of values')
                  self.errors.terminate() 
                commands[count] = [str('$(') + str(commands[count].replace(ID, value)) + str(')') for value in commandValues[ID][count]]

          self.nodeMethods.setGraphNodeAttribute(graph, nodeID, 'isCommandToEvaluate', True)
          self.nodeMethods.setGraphNodeAttribute(graph, nodeID, 'values', commands)

          # Create an edge between the nodes used for evaluating the command and this task.
          for optionNodeID in linkedNodeIDs:
            fileNodeIDs = self.nodeMethods.getAssociatedFileNodeIDs(graph, optionNodeID)
            self.edgeMethods.addEvaluateCommandEdge(graph, optionNodeID, task)
            self.edgeMethods.addEvaluateCommandEdge(graph, fileNodeIDs[0], task)

          # Record that the pipeline contains an argument that evaluated a command.
          self.hasCommandToEvaluate = True

  # Identify streaming file nodes.
  def identifyStreamingNodes(self, graph):
    for task in self.pipeline.workflow:

      # Parse the output nodes.
      if self.nodeMethods.getGraphNodeAttribute(graph, task, 'outputStream'):
        for fileNodeID in self.nodeMethods.getSuccessorFileNodes(graph, task):

          # Get the argument for this file node and check if this argument has the 'if output to stream'
          # option in the tool configuration file. Only one argument can have this option, so once it is
          # found, there is no need to check other file nodes for this task. If none of the arguments
          # have this option, terminate gkno, since the task is not able to output to a stream (the
          # configuration file would need to be updated to reflect this option).
          argument = self.edgeMethods.getEdgeAttribute(graph, task, fileNodeID, 'longFormArgument')
          tool     = self.nodeMethods.getGraphNodeAttribute(graph, task, 'tool')
          if self.tools.getArgumentAttribute(tool, argument, 'outputStream') != None:
            self.nodeMethods.setGraphNodeAttribute(graph, fileNodeID, 'isStreaming', True)

            # Mark the edges for this file node/option node -> task as streaming.
            optionNodeID = self.nodeMethods.getOptionNodeIDFromFileNodeID(fileNodeID)
            self.edgeMethods.setEdgeAttribute(graph, task, fileNodeID, 'isStreaming', True)
            self.edgeMethods.setEdgeAttribute(graph, optionNodeID, task, 'isStreaming', True)

            # Modify the command line argument associated with the nodes in order to handle the streaming
            # file.
            if self.edgeMethods.getEdgeAttribute(graph, optionNodeID, task, 'ifOutputIsStream') == 'do not include':
              self.edgeMethods.setEdgeAttribute(graph, optionNodeID, task, 'includeOnCommandLine', False)
              self.edgeMethods.setEdgeAttribute(graph, task, fileNodeID, 'includeOnCommandLine', False)

            # The previous steps marked the edges for the output of the task as streaming. This necessarily
            # means that the input to the next task is a stream and so the corresponding edges that use
            # this input stream must be marked.
            for successorTask in self.nodeMethods.getSuccessorTaskNodes(graph, fileNodeID):
              successorOptionNodeID = self.nodeMethods.getOptionNodeIDFromFileNodeID(fileNodeID)
              self.edgeMethods.setEdgeAttribute(graph, fileNodeID, successorTask, 'isStreaming', True)
              self.edgeMethods.setEdgeAttribute(graph, successorOptionNodeID, successorTask, 'isStreaming', True)

            #TODO CHECK IF I NEED TO MODIFY COMMAND LINE ARGUMENTS HERE.
            break

  # Search for unset flag nodes and set the values to 'unset'.
  def searchForUnsetFlags(self, graph):
    for nodeID in self.nodeMethods.getNodes(graph, 'option'):
      if self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'dataType') == 'flag':
        if not self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'values'):
          self.nodeMethods.addValuesToGraphNode(graph, nodeID, ['unset'], write = 'replace')

  # Export a parameter set to file.
  def exportParameterSet(self, graph, path, runName, isPipeline):
    isVerbose = self.nodeMethods.getGraphNodeAttribute(graph, 'GKNO-VERBOSE', 'values')[1][0]

    # Check that only a single parameter set was specified and get the requested parameter set name.
    if len(self.nodeMethods.getGraphNodeAttribute(graph, 'GKNO-EXPORT-PARAMETER-SET', 'values')[1]) > 1:
      self.errors.exportParameterSetSetMultipleTimes(runName, isVerbose)

    # Set the filename and the parameter set name.
    filename         = runName + '_parameterSets.json'
    parameterSetName = self.nodeMethods.getGraphNodeAttribute(graph, 'GKNO-EXPORT-PARAMETER-SET', 'values')[1][0]

    # If no parameter set name was provided, or the parameter set already exists, fail.
    if parameterSetName == '': self.errors.noParameterSetNameInExport(filename, parameterSetName, isVerbose)
    if parameterSetName in self.parameterSets.parameterSetAttributes[runName]: self.errors.parameterSetNameExists(parameterSetName, isVerbose)

    # Get all of the arguments set by the user.
    if isPipeline: arguments = self.getAllPipelineArguments(graph)
    else: arguments = self.getAllToolArguments(graph, runName)
    if not arguments: self.errors.noInformationProvidedForParameterSet(isVerbose)
    self.parameterSets.writeNewConfigurationFile(arguments, path, filename, runName, parameterSetName)

  # Get all of the argument data ready for sending to the parameter set file.
  def getAllPipelineArguments(self, graph):
    arguments = []
    for argument in self.pipeline.pipelineArguments:
      nodeID        = self.pipeline.pipelineArguments[argument].ID
      values        = self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'values')
      isConstructed = self.nodeMethods.getGraphNodeAttribute(graph, nodeID, 'isConstructed')

      # Only store arguments that were set by the user, not constructed.
      if not isConstructed and values: arguments.append((str(argument), values))

    return arguments 

  # Get all of the argument data for the tool.
  def getAllToolArguments(self, graph, runName):
    arguments = []
    for optionNodeID in self.nodeMethods.getPredecessorOptionNodes(graph, runName):
      argument      = self.edgeMethods.getEdgeAttribute(graph, optionNodeID, runName, 'longFormArgument')
      values        = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'values')
      isConstructed = self.nodeMethods.getGraphNodeAttribute(graph, optionNodeID, 'isConstructed')
      if not isConstructed and values: arguments.append((str(argument), values))

    return arguments

  # Check that all pipeline arguments listed as required were set.
  def checkArguments(self, graph, commandLine, runName, parameterSetName, hasMultipleRuns, loopData):
    for longFormArgument in self.pipeline.pipelineArguments:
      isSet             = False
      shortFormArgument = self.pipeline.pipelineArguments[longFormArgument].shortFormArgument
      description       = self.pipeline.pipelineArguments[longFormArgument].description
      if self.pipeline.pipelineArguments[longFormArgument].isRequired:

        # Check if this required pipeline argument was set on the command line.
        if longFormArgument in commandLine.argumentDictionary: isSet = True

        # If the argument wasn't set on the command line, check to see if it was set using a parameter set.
        # First check the default parameter set.
        if not isSet:
          for node in self.parameterSets.parameterSetAttributes[runName]['default'].nodes:
            if longFormArgument == node.argument: isSet = True

        # Then, if necessary, check the requested parameter set.
        if not isSet and parameterSetName != 'default':
          for node in self.parameterSets.parameterSetAttributes[runName][parameterSetName].nodes:
            if longFormArgument == node.argument: isSet = True

        # If necessary, check if multiple runs/internal loops are being used and the argument is set
        # in the provided file.
        if not isSet and hasMultipleRuns:
          if longFormArgument in loopData.argumentValues.keys(): isSet = True

        # Finally check if instructions were provided for evaluating a command in lieu of values.
        if self.pipeline.nodeAttributes[self.pipeline.pipelineArguments[longFormArgument].configNodeID].evaluateCommand: isSet = True

        # If the argument was not set, terminate.
        if not isSet: self.errors.unsetFile(longFormArgument, shortFormArgument, description, [], True)
