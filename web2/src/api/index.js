// The only module that screens import for data. No fetch in a component, ever.
//
// Every call goes to the real backend. VITE_API=fixtures swaps in sample data
// for UI work with no server running. Live is the default on purpose: the
// switch used to live in web2/.env, which the root .gitignore's `.env` rule
// kept out of git, so every fresh clone and image shipped the demo data. The
// names below are the contract both sides implement.
import * as fixtures from './fixtures.js'
import * as live from './live.js'

const impl = import.meta.env.VITE_API === 'fixtures' ? fixtures : live

export const isLive = impl === live

export const listProjects = impl.listProjects
export const createProject = impl.createProject
export const renameProject = impl.renameProject
export const deleteProject = impl.deleteProject

export const listConnections = impl.listConnections
export const createConnection = impl.createConnection
export const stageUpload = impl.stageUpload
export const syncConnection = impl.syncConnection
export const ingestUnits = impl.ingestUnits
export const retryUnits = impl.retryUnits
export const fetchLinks = impl.fetchLinks
export const pipelineRuns = impl.pipelineRuns
export const sourceFailures = impl.sourceFailures
export const removeConnection = impl.removeConnection
export const connectionPolicy = impl.connectionPolicy
export const saveConnectionPolicy = impl.saveConnectionPolicy
export const pipelineStatus = impl.pipelineStatus
export const retrySources = impl.retrySources
export const setSourcePolicy = impl.setSourcePolicy

export const listSources = impl.listSources
export const listSourceGroups = impl.listSourceGroups
export const listSubfolders = impl.listSubfolders
export const sourceArticles = impl.sourceArticles
export const viewSource = impl.viewSource
export const sourceContent = impl.sourceContent

export const sourcesByUrl = impl.sourcesByUrl
export const queueSources = impl.queueSources
export const wikiQueue = impl.wikiQueue
export const startWikiWriteUp = impl.startWikiWriteUp

export const listTimeline = impl.listTimeline

export const listPeople = impl.listPeople
export const personEvents = impl.personEvents

export const listConversations = impl.listConversations
export const conversationMessages = impl.conversationMessages
export const conversationMessagesFull = impl.conversationMessagesFull
export const sendMessage = impl.sendMessage
export const STARTERS = impl.STARTERS
export const CONNECTOR_CATALOGUE = impl.CONNECTOR_CATALOGUE
export const deleteConversation = impl.deleteConversation

export const search = impl.search

export const wikiGraph = impl.wikiGraph
export const wikiArticle = impl.wikiArticle
export const deleteArticle = impl.deleteArticle

export const getSettings = impl.getSettings
export const saveProvider = impl.saveProvider
export const testProvider = impl.testProvider
export const getOllamaStatus = impl.getOllamaStatus

export const health = impl.health
export const pipelineEstimate = impl.pipelineEstimate
export const startPipelineRun = impl.startPipelineRun
export const pipelineRun = impl.pipelineRun
export const pipelineRunLog = impl.pipelineRunLog
export const stopPipelineRun = impl.stopPipelineRun
export const retryPipelineRun = impl.retryPipelineRun

export const listRepos = impl.listRepos
export const checkRepo = impl.checkRepo
export const addRepo = impl.addRepo
export const removeRepo = impl.removeRepo
export const runRepoStep = impl.runRepoStep
export const sweepRepos = impl.sweepRepos
export const repoCommits = impl.repoCommits
export const repoQueue = impl.repoQueue
