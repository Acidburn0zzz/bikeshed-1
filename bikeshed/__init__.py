# -*- coding: utf-8 -*-

from __future__ import division, unicode_literals
import re
from collections import defaultdict, OrderedDict
import io
import os
import sys
import json
import urllib
import logging
import argparse
import itertools
import collections
from datetime import datetime

from . import config
from . import biblio
from . import update
from . import markdown
from . import test
from . import MetadataManager as metadata
from . import HTMLSerializer
from . import headings
from . import shorthands
from . import boilerplate
from . import datablocks
from . import publish
from . import extensions
from . import lint
from . import caniuse
from . import highlight
from .requests import requests
from .ReferenceManager import ReferenceManager
from .htmlhelpers import *
from .messages import *
from .widlparser.widlparser import parser


def main():
    # Hack around argparse's lack of optional subparsers
    if len(sys.argv) == 1:
        sys.argv.append("spec")

    argparser = argparse.ArgumentParser(description="Processes spec source files into valid HTML.")
    argparser.add_argument("-q", "--quiet", dest="quiet", action="count", default=0,
                           help="Silences one level of message, least-important first.")
    argparser.add_argument("-s", "--silent", dest="silent", action="store_true",
                           help="Shorthand for 'as many -q as you need to shut it up'")
    argparser.add_argument("-f", "--force", dest="force", action="store_true",
                           help="Force the preprocessor to run to completion; fatal errors don't stop processing.")
    argparser.add_argument("-d", "--dry-run", dest="dryRun", action="store_true",
                           help="Prevents the processor from actually saving anything to disk, but otherwise fully runs.")
    argparser.add_argument("--print", dest="printMode", action="store", default="console",
                           help="Print mode. Options are 'plain' (just text), 'console' (colored with console color codes), 'markup', and 'json'.")

    subparsers = argparser.add_subparsers(title="Subcommands", dest='subparserName')

    specParser = subparsers.add_parser('spec', help="Process a spec source file into a valid output file.")
    specParser.add_argument("infile", nargs="?",
                            default=None,
                            help="Path to the source file.")
    specParser.add_argument("outfile", nargs="?",
                            default=None,
                            help="Path to the output file.")
    specParser.add_argument("--debug", dest="debug", action="store_true", help="Switches on some debugging tools. Don't use for production!")
    specParser.add_argument("--gh-token", dest="ghToken", nargs="?",
                            help="GitHub access token. Useful to avoid API rate limits. Generate tokens: https://github.com/settings/tokens.")
    specParser.add_argument("--byos", dest="byos", action="store_true",
                            help="Bring-Your-Own-Spec: turns off all the Bikeshed auto-niceties, so you can piecemeal its features into your existing doc instead. Experimental, let me know if things get crashy or weird.")
    specParser.add_argument("-l", "--line-numbers", dest="lineNumbers", action="store_true",
                            help="Hacky support for outputting line numbers on all error messages. Disables output, as this is hacky and might mess up your source.")

    echidnaParser = subparsers.add_parser('echidna', help="Process a spec source file into a valid output file and publish it according to certain automatic protocols.")
    echidnaParser.add_argument("infile", nargs="?",
                               default=None,
                               help="Path to the source file.")
    echidnaParser.add_argument("--gh-token", dest="ghToken", nargs="?",
                               help="GitHub access token. Useful to avoid API rate limits. Generate tokens: https://github.com/settings/tokens.")
    echidnaParser.add_argument("--u", dest="un", metavar="USERNAME", required=False, help="W3C username.")
    echidnaParser.add_argument("--p", dest="pw", metavar="PASSWORD", required=False, help="W3C password.")
    echidnaParser.add_argument("--d", dest="decision", metavar="DECISION_URL", required=False, help="URL recording the decision to publish.")
    echidnaParser.add_argument("--additional-directories", dest="additionalDirectories", required=False, nargs="*", help="Directories to bundle in the tar file. Defaults to examples/, diagrams/, and images/.")
    echidnaParser.add_argument("--self-contained", dest="selfContained", action="store_true", help="The spec is self-contained, do not bundle any extra directories in the tar file.")
    echidnaParser.add_argument("--just-tar", dest="justTar", action="store_true")

    watchParser = subparsers.add_parser('watch', help="Process a spec source file into a valid output file, automatically rebuilding when it changes.")
    watchParser.add_argument("infile", nargs="?",
                             default=None,
                             help="Path to the source file.")
    watchParser.add_argument("outfile", nargs="?",
                             default=None,
                             help="Path to the output file.")
    watchParser.add_argument("--gh-token", dest="ghToken", nargs="?",
                             help="GitHub access token. Useful to avoid API rate limits. Generate tokens: https://github.com/settings/tokens.")
    watchParser.add_argument("--byos", dest="byos", action="store_true",
                             help="Bring-Your-Own-Spec: turns off all the Bikeshed auto-niceties, so you can piecemeal its features into your existing doc instead. Experimental, let me know if things get crashy or weird.")


    serveParser = subparsers.add_parser('serve', help="Identical to 'watch', but also serves the folder on localhost.")
    serveParser.add_argument("infile", nargs="?",
                             default=None,
                             help="Path to the source file.")
    serveParser.add_argument("outfile", nargs="?",
                             default=None,
                             help="Path to the output file.")
    serveParser.add_argument("--port", dest="port", nargs="?", default="8000",
                             help="Specify the port to serve it over.")
    serveParser.add_argument("--gh-token", dest="ghToken", nargs="?",
                             help="GitHub access token. Useful to avoid API rate limits. Generate tokens: https://github.com/settings/tokens.")
    serveParser.add_argument("--byos", dest="byos", action="store_true",
                             help="Bring-Your-Own-Spec: turns off all the Bikeshed auto-niceties, so you can piecemeal its features into your existing doc instead. Experimental, let me know if things get crashy or weird.")

    updateParser = subparsers.add_parser('update', help="Update supporting files (those in /spec-data).", epilog="If no options are specified, everything is downloaded.")
    updateParser.add_argument("--anchors", action="store_true", help="Download crossref anchor data.")
    updateParser.add_argument("--biblio", action="store_true", help="Download biblio data.")
    updateParser.add_argument("--caniuse", action="store_true", help="Download Can I Use... data.")
    updateParser.add_argument("--link-defaults", dest="linkDefaults", action="store_true", help="Download link default data.")
    updateParser.add_argument("--test-suites", dest="testSuites", action="store_true", help="Download test suite data.")
    updateParser.add_argument("--languages", dest="languages", action="store_true", help="Download language/translation data.")

    issueParser = subparsers.add_parser('issues-list', help="Process a plain-text issues file into HTML. Call with no args to see an example input text.")
    issueParser.add_argument("-t",
                             dest="printTemplate",
                             action="store_true",
                             help="Output example Issues List template.")
    issueParser.add_argument("infile", nargs="?",
                             default=None,
                             help="Path to the plain-text issue file.")
    issueParser.add_argument("outfile", nargs="?",
                             default=None,
                             help="Path to the output file. Default is file of the same name as input, with .html.")

    debugParser = subparsers.add_parser('debug', help="Run various debugging commands.")
    debugParser.add_argument("infile", nargs="?",
                             default=None,
                             help="Path to the source file.")
    debugCommands = debugParser.add_mutually_exclusive_group(required=True)
    debugCommands.add_argument("--print-exports", dest="printExports", action="store_true",
                               help="Prints those terms that will be exported for cross-ref purposes.")
    debugCommands.add_argument("--print-refs-for", dest="linkText",
                               help="Prints the ref data for a given link text.")
    debugCommands.add_argument("--print", dest="code",
                               help="Runs the specified code and prints it.")
    debugCommands.add_argument("--print-json", dest="jsonCode",
                               help="Runs the specified code and prints it as formatted JSON.")

    refParser = subparsers.add_parser('refs', help="Search Bikeshed's ref database.")
    refParser.add_argument("infile", nargs="?",
                           default=None,
                           help="Path to the source file.")
    refParser.add_argument("--text", dest="text", default=None)
    refParser.add_argument("--type", dest="linkType", default=None)
    refParser.add_argument("--for", dest="linkFor", default=None)
    refParser.add_argument("--spec", dest="spec", default=None)
    refParser.add_argument("--status", dest="status", default=None)
    refParser.add_argument("--exact", dest="exact", action="store_true")

    sourceParser = subparsers.add_parser('source', help="Tools for formatting the *source* document.")
    sourceParser.add_argument("--big-text",
                              dest="bigText",
                              action="store_true",
                              help="Finds HTML comments containing 'Big Text: foo' and turns them into comments containing 'foo' in big text.")
    sourceParser.add_argument("infile", nargs="?",
                              default=None,
                              help="Path to the source file.")
    sourceParser.add_argument("outfile", nargs="?",
                              default=None,
                              help="Path to the output file.")

    testParser = subparsers.add_parser('test', help="Tools for running Bikeshed's testsuite.")
    testParser.add_argument("--rebase",
                            default=False,
                            action="store_true",
                            help="Rebase the specified files.")
    testParser.add_argument('testFiles',
                            default=[],
                            metavar="FILE",
                            nargs="*",
                            help="Run these tests. If called with no args, tests everything.")

    profileParser = subparsers.add_parser('profile', help="Profiling Bikeshed. Needs graphviz, gprof2dot, and xdot installed.")
    profileParser.add_argument("--root",
                               dest="root",
                               default=None,
                               metavar="ROOTFUNC",
                               help="Prune the graph to start with the specified root node.")
    profileParser.add_argument("--leaf",
                               dest="leaf",
                               default=None,
                               metavar="LEAFFUNC",
                               help="Prune the graph to only show ancestors of the specified leaf node.")
    profileParser.add_argument("--svg", dest="svgFile", default=None, help="Save the graph to a specified SVG file, rather than outputting with xdot immediately.")

    templateParser = subparsers.add_parser('template', help="Outputs a skeleton .bs file for you to start with.")

    options, extras = argparser.parse_known_args()

    config.quiet = options.quiet
    if options.silent:
        config.quiet = float("infinity")
    config.force = options.force
    config.dryRun = options.dryRun
    config.printMode = options.printMode

    update.fixupDataFiles()
    if options.subparserName == "update":
        update.update(anchors=options.anchors, biblio=options.biblio, caniuse=options.caniuse, linkDefaults=options.linkDefaults, testSuites=options.testSuites, languages=options.languages)
    elif options.subparserName == "spec":
        doc = Spec(inputFilename=options.infile, debug=options.debug, token=options.ghToken, lineNumbers=options.lineNumbers)
        doc.md = metadata.fromCommandLine(extras, doc)
        if options.byos:
            doc.md.addData("Group", "byos")
        doc.preprocess()
        doc.finish(outputFilename=options.outfile)
    elif options.subparserName == "echidna":
        doc = Spec(inputFilename=options.infile, token=options.ghToken)
        doc.md = metadata.fromCommandLine(extras, doc)
        doc.md.addData("Prepare For TR", "yes")
        doc.preprocess()
        addDirs = [] if options.selfContained else options.additionalDirectories
        if options.justTar:
            publish.prepareTar(doc, visibleTar=True, additionalDirectories=addDirs)
        else:
            publish.publishEchidna(doc, username=options.un, password=options.pw, decision=options.decision, additionalDirectories=addDirs)
    elif options.subparserName == "watch":
        # Can't have an error killing the watcher
        config.force = True
        doc = Spec(inputFilename=options.infile, token=options.ghToken)
        if options.byos:
            doc.md.addData("Group", "byos")
        doc.watch(outputFilename=options.outfile)
    elif options.subparserName == "serve":
        config.force = True
        doc = Spec(inputFilename=options.infile, token=options.ghToken)
        if options.byos:
            doc.md.addData("Group", "byos")
        doc.watch(outputFilename=options.outfile, port=int(options.port))
    elif options.subparserName == "debug":
        config.force = True
        config.quiet = 2
        if options.printExports:
            doc = Spec(inputFilename=options.infile)
            doc.preprocess()
            doc.printTargets()
        elif options.jsonCode:
            doc = Spec(inputFilename=options.infile)
            doc.preprocess()
            exec("print config.printjson({0})".format(options.jsonCode))
        elif options.code:
            doc = Spec(inputFilename=options.infile)
            doc.preprocess()
            exec("print {0}".format(options.code))
        elif options.linkText:
            doc = Spec(inputFilename=options.infile)
            doc.preprocess()
            refs = doc.refs.refs[options.linkText] + doc.refs.refs[options.linkText + "\n"]
            config.quiet = options.quiet
            if not config.quiet:
                p("Refs for '{0}':".format(options.linkText))
            # Get ready for JSONing
            for ref in refs:
                ref['level'] = str(ref['level'])
            p(config.printjson(refs))
    elif options.subparserName == "refs":
        config.force = True
        config.quiet = 10
        doc = Spec(inputFilename=options.infile)
        if doc.valid:
            doc.preprocess()
            rm = doc.refs
        else:
            rm = ReferenceManager()
            rm.initializeRefs()
        if options.text:
            options.text = unicode(options.text, encoding="utf-8")
        refs = rm.queryAllRefs(text=options.text, linkFor=options.linkFor, linkType=options.linkType, status=options.status, spec=options.spec, exact=options.exact)
        if config.printMode == "json":
            p(json.dumps(refs, indent=2, default=config.getjson))
        else:
            p(config.printjson(refs))
    elif options.subparserName == "issues-list":
        from . import issuelist as il
        if options.printTemplate:
            il.printHelpMessage()
        else:
            il.printIssueList(options.infile, options.outfile)
    elif options.subparserName == "source":
        if not options.bigText:  # If no options are given, do all options.
            options.bigText = True
        if options.bigText:
            from . import fonts
            font = fonts.Font()
            fonts.replaceComments(font=font, inputFilename=options.infile, outputFilename=options.outfile)
    elif options.subparserName == "test":
        if options.rebase:
            test.rebase(options.testFiles)
        else:
            config.force = True
            config.quiet = 2
            result = test.runAllTests(Spec, options.testFiles)
            sys.exit(0 if result else 1)
    elif options.subparserName == "profile":
        root = "--root=\"{0}\"".format(options.root) if options.root else ""
        leaf = "--leaf=\"{0}\"".format(options.leaf) if options.leaf else ""
        if options.svgFile:
            os.system("python -m cProfile -o stat.prof ~/bikeshed/bikeshed.py && gprof2dot -f pstats --skew=.0001 {root} {leaf} stat.prof | dot -Tsvg -o {svg} && rm stat.prof".format(root=root, leaf=leaf, svg=options.svgFile))
        else:
            os.system("python -m cProfile -o /tmp/stat.prof ~/bikeshed/bikeshed.py && gprof2dot -f pstats --skew=.0001 {root} {leaf} /tmp/stat.prof | xdot &".format(root=root, leaf=leaf))
    elif options.subparserName == "template":
        p('''<pre class='metadata'>
Title: Your Spec Title
Shortname: your-spec
Level: 1
Status: ED
Group: WGNAMEORWHATEVER
URL: http://example.com/url-this-spec-will-live-at
Editor: Your Name, Your Company http://example.com/your-company, your-email@example.com, http://example.com/your-personal-website
Abstract: A short description of your spec, one or two sentences.
</pre>

Introduction {#intro}
=====================

Introduction here.
''')


class Spec(object):

    def __init__(self, inputFilename, debug=False, token=None, lineNumbers=False):
        self.valid = False
        self.lineNumbers = lineNumbers
        if lineNumbers:
            # line-numbers are too hacky, so force this to be a dry run
            config.dryRun = True
        if inputFilename is None:
            # Default to looking for a *.bs file.
            # Otherwise, look for a *.src.html file.
            # Otherwise, use standard input.
            import glob
            if glob.glob("*.bs"):
                inputFilename = glob.glob("*.bs")[0]
            elif glob.glob("*.src.html"):
                inputFilename = glob.glob("*.src.html")[0]
            else:
                die("No input file specified, and no *.bs or *.src.html files found in current directory.\nPlease specify an input file, or use - to pipe from STDIN.")
                return
        self.inputSource = inputFilename
        self.debug = debug
        self.token = token

        self.valid = self.initializeState()

    def initializeState(self):
        self.normativeRefs = {}
        self.informativeRefs = {}
        self.refs = ReferenceManager()
        self.externalRefsUsed = defaultdict(lambda:defaultdict(dict))
        self.md = metadata.MetadataManager(doc=self)
        self.biblios = {}
        self.typeExpansions = {}
        self.macros = defaultdict(lambda x: "???")
        self.canIUse = json.loads(config.retrieveDataFile("caniuse.json", quiet=True, str=True), object_pairs_hook=OrderedDict)
        self.widl = parser.Parser(ui=IDLSilent())
        self.testSuites = json.loads(config.retrieveDataFile("test-suites.json", quiet=True, str=True))
        self.languages = json.loads(config.retrieveDataFile("languages.json", quiet=True, str=True))
        self.extraStyles = defaultdict(str)
        self.extraStyles['style-md-lists'] = '''
            /* This is a weird hack for me not yet following the commonmark spec
               regarding paragraph and lists. */
            [data-md] > :first-child {
                margin-top: 0;
            }
            [data-md] > :last-child {
                margin-bottom: 0;
            }'''
        self.extraStyles['style-autolinks'] = '''
            .css.css, .property.property, .descriptor.descriptor {
                color: #005a9c;
                font-size: inherit;
                font-family: inherit;
            }
            .css::before, .property::before, .descriptor::before {
                content: "‘";
            }
            .css::after, .property::after, .descriptor::after {
                content: "’";
            }
            .property, .descriptor {
                /* Don't wrap property and descriptor names */
                white-space: nowrap;
            }
            .type { /* CSS value <type> */
                font-style: italic;
            }
            pre .property::before, pre .property::after {
                content: "";
            }
            [data-link-type="property"]::before,
            [data-link-type="propdesc"]::before,
            [data-link-type="descriptor"]::before,
            [data-link-type="value"]::before,
            [data-link-type="function"]::before,
            [data-link-type="at-rule"]::before,
            [data-link-type="selector"]::before,
            [data-link-type="maybe"]::before {
                content: "‘";
            }
            [data-link-type="property"]::after,
            [data-link-type="propdesc"]::after,
            [data-link-type="descriptor"]::after,
            [data-link-type="value"]::after,
            [data-link-type="function"]::after,
            [data-link-type="at-rule"]::after,
            [data-link-type="selector"]::after,
            [data-link-type="maybe"]::after {
                content: "’";
            }

            [data-link-type].production::before,
            [data-link-type].production::after,
            .prod [data-link-type]::before,
            .prod [data-link-type]::after {
                content: "";
            }

            [data-link-type=element],
            [data-link-type=element-attr] {
                font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace;
                font-size: .9em;
            }
            [data-link-type=element]::before { content: "<" }
            [data-link-type=element]::after  { content: ">" }

            [data-link-type=biblio] {
                white-space: pre;
            }'''
        self.extraStyles['style-selflinks'] = '''
            .heading, .issue, .note, .example, li, dt {
                position: relative;
            }
            a.self-link {
                position: absolute;
                top: 0;
                left: calc(-1 * (3.5rem - 26px));
                width: calc(3.5rem - 26px);
                height: 2em;
                text-align: center;
                border: none;
                transition: opacity .2s;
                opacity: .5;
            }
            a.self-link:hover {
                opacity: 1;
            }
            .heading > a.self-link {
                font-size: 83%;
            }
            li > a.self-link {
                left: calc(-1 * (3.5rem - 26px) - 2em);
            }
            dfn > a.self-link {
                top: auto;
                left: auto;
                opacity: 0;
                width: 1.5em;
                height: 1.5em;
                background: gray;
                color: white;
                font-style: normal;
                transition: opacity .2s, background-color .2s, color .2s;
            }
            dfn:hover > a.self-link {
                opacity: 1;
            }
            dfn > a.self-link:hover {
                color: black;
            }

            a.self-link::before            { content: "¶"; }
            .heading > a.self-link::before { content: "§"; }
            dfn > a.self-link::before      { content: "#"; }'''
        self.extraStyles['style-counters'] = '''
            body {
                counter-reset: example figure issue;
            }
            .issue {
                counter-increment: issue;
            }
            .issue:not(.no-marker)::before {
                content: "Issue " counter(issue);
            }

            .example {
                counter-increment: example;
            }
            .example:not(.no-marker)::before {
                content: "Example " counter(example);
            }
            .invalid.example:not(.no-marker)::before,
            .illegal.example:not(.no-marker)::before {
                content: "Invalid Example" counter(example);
            }

            figcaption {
                counter-increment: figure;
            }
            figcaption:not(.no-marker)::before {
                content: "Figure " counter(figure) " ";
            }'''
        self.extraScripts = defaultdict(str)

        try:
            if self.inputSource == "-":
                self.lines = [unicode(line, encoding="utf-8") for line in sys.stdin.readlines()]
                self.md.date = datetime.today()
            else:
                self.lines = io.open(self.inputSource, 'r', encoding="utf-8").readlines()
                self.md.date = datetime.fromtimestamp(os.path.getmtime(self.inputSource))
        except OSError:
            die("Couldn't find the input file at the specified location '{0}'.", self.inputSource)
            return False
        except IOError:
            die("Couldn't open the input file '{0}'.", self.inputSource)
            return False
        return True

    def preprocess(self):
        # Textual hacks
        stripBOM(self)
        if self.lineNumbers:
            self.lines = hackyLineNumbers(self.lines)
        self.lines = markdown.stripComments(self.lines)

        # Extract and process metadata
        self.lines, documentMd = metadata.parse(lines=self.lines, doc=self)
        self.md = metadata.join(documentMd, self.md)
        defaultMd = metadata.fromJson(data=config.retrieveBoilerplateFile(self, 'defaults', error=True), doc=self)
        self.md = metadata.join(defaultMd, self.md)
        if self.md.group == "byos":
            self.md.boilerplate.default = False
        self.md.finish()
        extensions.load(self)
        self.md.fillTextMacros(self.macros, doc=self)

        # Initialize things
        self.refs.initializeRefs(self)
        self.refs.initializeBiblio()

        # Deal with further <pre> blocks, and markdown
        self.lines = datablocks.transformDataBlocks(self, self.lines)
        self.lines = markdown.parse(self.lines, self.md.indent, opaqueElements=self.md.opaqueElements, blockElements=self.md.blockElements)

        self.refs.setSpecData(self.md)

        # Convert to a single string of html now, for convenience.
        self.html = ''.join(self.lines)
        boilerplate.addHeaderFooter(self)
        self.html = self.fixText(self.html)

        # Build the document
        self.document = parseDocument(self.html)
        self.head = find("head", self)
        self.body = find("body", self)
        correctH1(self)
        processInclusions(self)
        metadata.parseDoc(self)

        # Fill in and clean up a bunch of data
        self.fillContainers = locateFillContainers(self)
        lint.lintExampleIDs(self)
        boilerplate.addBikeshedVersion(self)
        boilerplate.addStatusSection(self)
        boilerplate.addLogo(self)
        boilerplate.addCopyright(self)
        boilerplate.addSpecMetadataSection(self)
        boilerplate.addAbstract(self)
        boilerplate.addObsoletionNotice(self)
        boilerplate.addAtRisk(self)
        addNoteHeaders(self)
        addImplicitAlgorithms(self)
        boilerplate.removeUnwantedBoilerplate(self)
        shorthands.transformProductionPlaceholders(self)
        shorthands.transformMaybePlaceholders(self)
        shorthands.transformAutolinkShortcuts(self)
        shorthands.transformProductionGrammars(self)
        canonicalizeShortcuts(self)
        fixManualDefTables(self)
        headings.processHeadings(self)
        checkVarHygiene(self)
        processIssuesAndExamples(self)
        markupIDL(self)
        inlineRemoteIssues(self)

        # Handle all the links
        processBiblioLinks(self)
        processDfns(self)
        processIDL(self)
        fillAttributeInfoSpans(self)
        formatArgumentdefTables(self)
        formatElementdefTables(self)
        processAutolinks(self)
        caniuse.addCanIUsePanels(self)
        boilerplate.addIndexSection(self)
        boilerplate.addExplicitIndexes(self)
        boilerplate.addStyles(self)
        boilerplate.addReferencesSection(self)
        boilerplate.addPropertyIndex(self)
        boilerplate.addIDLSection(self)
        boilerplate.addIssuesSection(self)
        boilerplate.addCustomBoilerplate(self)
        headings.processHeadings(self, "all")  # again
        boilerplate.removeUnwantedBoilerplate(self)
        boilerplate.addTOCSection(self)
        addSelfLinks(self)
        processAutolinks(self)
        boilerplate.addAnnotations(self)
        boilerplate.removeUnwantedBoilerplate(self)
        highlight.addSyntaxHighlighting(self)
        boilerplate.addBikeshedBoilerplate(self)
        fixIntraDocumentReferences(self)
        fixInterDocumentReferences(self)
        lint.lintBrokenLinks(self)

        # Any final HTML cleanups
        cleanupHTML(self)
        if self.md.prepTR:
            # Don't try and override the W3C's icon.
            for el in findAll("[rel ~= 'icon']", self):
                removeNode(el)
            # Make sure the W3C stylesheet is after all other styles.
            for el in findAll("link", self):
                if el.get("href").startswith("https://www.w3.org/StyleSheets/TR"):
                    appendChild(find("head", self), el)
            # Ensure that all W3C links are https.
            for el in findAll("a", self):
                href = el.get("href", "")
                if href.startswith("http://www.w3.org") or href.startswith("http://lists.w3.org"):
                    el.set("href", "https" + href[4:])
                text = el.text or ""
                if text.startswith("http://www.w3.org") or text.startswith("http://lists.w3.org"):
                    el.text = "https" + text[4:]
            extensions.BSPrepTR(self)

        return self

    def serialize(self):
        rendered = HTMLSerializer.HTMLSerializer(self.document, self.md.opaqueElements, self.md.blockElements).serialize()
        rendered = finalHackyCleanup(rendered)
        return rendered

    def fixMissingOutputFilename(self, outputFilename):
        if outputFilename is None:
            # More sensible defaults!
            if self.inputSource.endswith(".bs"):
                outputFilename = self.inputSource[0:-3] + ".html"
            elif self.inputSource.endswith(".src.html"):
                outputFilename = self.inputSource[0:-9] + ".html"
            elif self.inputSource == "-":
                outputFilename = "-"
            else:
                outputFilename = "-"
        return outputFilename

    def finish(self, outputFilename):
        self.printResultMessage()
        outputFilename = self.fixMissingOutputFilename(outputFilename)
        rendered = self.serialize()
        if not config.dryRun:
            try:
                if outputFilename == "-":
                    sys.stdout.write(rendered.encode("utf-8"))
                else:
                    with io.open(outputFilename, "w", encoding="utf-8") as f:
                        f.write(rendered)
            except Exception, e:
                die("Something prevented me from saving the output document to {0}:\n{1}", outputFilename, e)

    def printResultMessage(self):
        # If I reach this point, I've succeeded, but maybe with reservations.
        fatals = messageCounts['fatal']
        links = messageCounts['linkerror']
        warnings = messageCounts['warning']
        if fatals:
            success("Successfully generated, but fatal errors were suppressed")
            return
        if links:
            success("Successfully generated, with {0} linking errors", links)
            return
        if warnings:
            success("Successfully generated, with warnings")
            return

    def watch(self, outputFilename, port=None):
        import time
        outputFilename = self.fixMissingOutputFilename(outputFilename)
        if self.inputSource == "-" or outputFilename == "-":
            die("Watch mode doesn't support streaming from STDIN or to STDOUT.")
            return

        if port:
            # Serve the folder on an HTTP server
            import SimpleHTTPServer
            import SocketServer
            import threading

            class SilentServer(SimpleHTTPServer.SimpleHTTPRequestHandler):
                def log_message(*args):
                    pass

            SocketServer.TCPServer.allow_reuse_address = True
            server = SocketServer.TCPServer(("", port), SilentServer)

            print "Serving at port {0}".format(port)
            thread = threading.Thread(target = server.serve_forever)
            thread.daemon = True
            thread.start()
        else:
            server = None

        try:
            lastInputModified = os.stat(self.inputSource).st_mtime
            self.preprocess()
            self.finish(outputFilename)
            p("==============DONE==============")
            try:
                while(True):
                    inputModified = os.stat(self.inputSource).st_mtime
                    if inputModified > lastInputModified:
                        resetSeenMessages()
                        lastInputModified = inputModified
                        formattedTime = datetime.fromtimestamp(inputModified).strftime("%H:%M:%S")
                        p("Source file modified at {0}. Rebuilding...".format(formattedTime))
                        self.initializeState()
                        self.preprocess()
                        self.finish(outputFilename)
                        p("==============DONE==============")
                    time.sleep(1)
            except KeyboardInterrupt:
                p("Exiting~")
                if server:
                    server.shutdown()
                    thread.join()
                sys.exit(0)
        except Exception, e:
            die("Something went wrong while watching the file:\n{0}", e)

    def fixText(self, text, moreMacros=None):
        # Do several textual replacements that need to happen *before* the document is parsed as HTML.

        # If markdown shorthands are on, remove all `foo`s while processing,
        # so their contents don't accidentally trigger other stuff.
        # Also handle markdown escapes.
        codeSpanReplacements = []
        if "markdown" in self.md.markupShorthands:
            newText = ""
            mode = "text"
            indexSoFar = 0
            escapeLen = 0
            for m in re.finditer(r"(\\`)|(`+)", text):
                if mode == "text":
                    if m.group(1):
                        newText += text[indexSoFar:m.start()] + m.group(1)[1]
                        indexSoFar = m.end()
                    elif m.group(2):
                        mode = "code"
                        newText += text[indexSoFar:m.start()]
                        indexSoFar = m.end()
                        escapeLen = len(m.group(2))
                elif mode == "code":
                    if m.group(1):
                        pass
                    elif m.group(2):
                        if len(m.group(2)) != escapeLen:
                            pass
                        else:
                            mode = "text"
                            codeSpanReplacements.append(text[indexSoFar:m.start()])
                            newText += "\ue0ff"
                            indexSoFar = m.end()
            if mode == "text":
                newText += text[indexSoFar:]
            elif mode == "code":
                newText += "`"*escapeLen + text[indexSoFar:]
            text = newText

        # Replace the [FOO] text macros.
        # [FOO?] macros are optional; failure just removes them.
        def macroReplacer(match):
            fullText = match.group(0)
            innerText = match.group(2).lower() or ""
            optional = match.group(3) == "?"
            if fullText.startswith("\\"):
                # Escaped
                return fullText[1:]
            if fullText.startswith("[["):
                # Actually a biblio link
                return fullText
            if re.match("[\d-]+$", innerText):
                # No refs are all-digits (this is probably JS code, or a regex/grammar).
                return fullText
            if innerText in self.macros:
                # For some reason I store all the macros in lowercase,
                # despite requiring them to be spelled with uppercase.
                return self.macros[innerText]
            if moreMacros and innerText in moreMacros:
                return moreMacros[innerText]
            # Nothing has matched, so start failing the macros.
            if optional:
                return ""
            die("Found unmatched text macro {0}. Correct the macro, or escape it with a leading backslash.", fullText)
            return fullText
        text = re.sub(r"(\\|\[)?\[([A-Z0-9-]+)(\??)\]", macroReplacer, text)
        text = fixTypography(text)
        if "css" in self.md.markupShorthands:
            # Replace the <<production>> shortcuts, because they won't survive the HTML parser.
            text = re.sub("<<([^>\s]+)>>", r"<fake-production-placeholder class=production>\1</fake-production-placeholder>", text)
            # Replace the ''maybe link'' shortcuts.
            # They'll survive the HTML parser, but they don't match if they contain an element.
            # (The other shortcuts are "atomic" and can't contain elements.)
            text = re.sub(r"''([^=\n]+?)''", r'<fake-maybe-placeholder>\1</fake-maybe-placeholder>', text)

        if codeSpanReplacements:
            codeSpanReplacements.reverse()

            def codeSpanReviver(_):
                # Match object is the PUA character, which I can ignore.
                # Instead, sub back the replacement in order,
                # massaged per the Commonmark rules.
                import string
                t = escapeHTML(codeSpanReplacements.pop()).strip(string.whitespace)
                t = re.sub("[" + string.whitespace + "]{2,}", " ", t)
                return "<code data-opaque>" + t + "</code>"
            text = re.sub("\ue0ff", codeSpanReviver, text)
        return text

    def printTargets(self):
        p("Exported terms:")
        for el in findAll("[data-export]", self):
            for term in config.linkTextsFromElement(el):
                p("  " + term)
        p("Unexported terms:")
        for el in findAll("[data-noexport]", self):
            for term in config.linkTextsFromElement(el):
                p("  " + term)

    def isOpaqueElement(self, el):
        if el.tag in self.md.opaqueElements:
            return True
        if el.get("data-opaque") is not None:
            return True
        return False

config.specClass = Spec


def stripBOM(doc):
    if len(doc.lines) >= 1 and doc.lines[0][0:1] == "\ufeff":
        doc.lines[0] = doc.lines[0][1:]
        warn("Your document has a BOM. There's no need for that, please re-save it without a BOM.")


# Definitions and the like

def fixManualDefTables(doc):
    # Def tables generated via datablocks are guaranteed correct,
    # but manually-written ones often don't link up the names in the first row.
    for table in findAll("table.propdef, table.descdef, table.elementdef", doc):
        if hasClass(table, "partial"):
            tag = "a"
            attr = "data-link-type"
        else:
            tag = "dfn"
            attr = "data-dfn-type"
        tag = "a" if hasClass(table, "partial") else "dfn"
        if hasClass(table, "propdef"):
            type = "property"
        elif hasClass(table, "descdef"):
            type = "descriptor"
        elif hasClass(table, "elementdef"):
            type = "element"
        cell = findAll("tr:first-child > :nth-child(2)", table)[0]
        names = [x.strip() for x in textContent(cell).split(',')]
        newContents = config.intersperse((createElement(tag, {attr:type}, name) for name in names), ", ")
        replaceContents(cell, newContents)


def canonicalizeShortcuts(doc):
    # Take all the invalid-HTML shortcuts and fix them.

    attrFixup = {
        "export":"data-export",
        "noexport":"data-noexport",
        "spec":"data-link-spec",
        "status":"data-link-status",
        "dfn-for":"data-dfn-for",
        "link-for":"data-link-for",
        "link-for-hint":"data-link-for-hint",
        "dfn-type":"data-dfn-type",
        "link-type":"data-link-type",
        "force":"data-dfn-force",
        "section":"data-section",
        "attribute-info":"data-attribute-info",
        "dict-member-info":"data-dict-member-info",
        "lt":"data-lt",
        "local-lt":"data-local-lt",
        "algorithm":"data-algorithm",
        "ignore":"data-var-ignore"
    }
    for el in findAll(",".join("[{0}]".format(attr) for attr in attrFixup.keys()), doc):
        for attr, fixedAttr in attrFixup.items():
            if el.get(attr) is not None:
                el.set(fixedAttr, el.get(attr))
                del el.attrib[attr]

    # The next two aren't in the above dict because some of the words conflict with existing attributes on some elements.
    # Instead, limit the search/transforms to the relevant elements.
    for el in findAll("dfn, h2, h3, h4, h5, h6", doc):
        for dfnType in config.dfnTypes:
            if el.get(dfnType) == "":
                del el.attrib[dfnType]
                el.set("data-dfn-type", dfnType)
                break
    for el in findAll("a", doc):
        for linkType in config.linkTypes:
            if el.get(linkType) is not None:
                del el.attrib[linkType]
                el.set("data-link-type", linkType)
                break
    for el in findAll(config.dfnElementsSelector + ", a", doc):
        if el.get("for") is None:
            continue
        if el.tag == "a":
            el.set("data-link-for", el.get('for'))
        else:
            el.set("data-dfn-for", el.get('for'))
        del el.attrib['for']


def addImplicitAlgorithms(doc):
    # If a container has an empty `algorithm` attribute,
    # but it contains only a single `<dfn>`,
    # assume that the dfn is a description of the algorithm.
    for el in findAll("[algorithm='']:not(h1):not(h2):not(h3):not(h4):not(h5):not(h6)", doc):
        dfns = findAll("dfn", el)
        if len(dfns) == 1:
            el.set("algorithm", config.firstLinkTextFromElement(dfns[0]))
        elif len(dfns) == 0:
            die("Algorithm container has no name, and there is no <dfn> to infer one from.", el=el)
        else:
            die("Algorithm container has no name, and there are too many <dfn>s to choose which to infer a name from.", el=el)


def checkVarHygiene(doc):
    def nearestAlgo(var):
        # Find the nearest "algorithm" container,
        # either an ancestor with [algorithm] or the nearest heading with same.
        algo = treeAttr(var, "data-algorithm")
        if algo:
            return algo or None
        for h in relevantHeadings(var):
            algo = h.get("data-algorithm")
            if algo is not None and algo is not "":
                return algo

    # Look for vars that only show up once. These are probably typos.
    singularVars = []
    varCounts = Counter((foldWhitespace(textContent(el)), nearestAlgo(el)) for el in findAll("var", doc) if el.get("data-var-ignore") is None)
    for var,count in varCounts.items():
        if count == 1 and var[0].lower() not in doc.md.ignoredVars:
            singularVars.append(var)
    if singularVars:
        printVars = ""
        for var,algo in singularVars:
            if algo:
                printVars += "  '{0}', in algorithm '{1}'\n".format(var, algo)
            else:
                printVars += "  '{0}'\n".format(var)
        warn("The following <var>s were only used once in the document:\n{0}If these are not typos, please add an ignore='' attribute to the <var>.", printVars)

    # Look for algorithms that show up twice; these are errors.
    for algo, count in Counter(el.get('data-algorithm') for el in findAll("[data-algorithm]", doc)).items():
        if count > 1:
            die("Multiple declarations of the '{0}' algorithm.", algo)
            return


def fixIntraDocumentReferences(doc):
    ids = {el.get('id'):el for el in findAll("[id]", doc)}
    headingIDs = {el.get('id'):el for el in findAll("[id].heading", doc)}
    for el in findAll("a[href^='#']:not([href='#']):not(.self-link):not([data-link-type])", doc):
        targetID = el.get("href")[1:]
        if el.get('data-section') is not None and targetID not in headingIDs:
            die("Couldn't find target document section {0}:\n{1}", targetID, outerHTML(el), el=el)
            continue
        elif targetID not in ids:
            die("Couldn't find target anchor {0}:\n{1}", targetID, outerHTML(el), el=el)
            continue
        if isEmpty(el):
            # TODO Allow this to respect "safe" markup (<sup>, etc) in the title
            target = ids[targetID]
            content = find(".content", target)
            if content is None:
                die("Tried to generate text for a section link, but the target isn't a heading:\n{0}", outerHTML(el), el=el)
                continue
            text = textContent(content).strip()
            if target.get('data-level') is not None:
                text = "§{1} {0}".format(text, target.get('data-level'))
            appendChild(el, text)


def fixInterDocumentReferences(doc):
    for el in findAll("[spec-section]", doc):
        spec = el.get('data-link-spec')
        section = el.get('spec-section', '')
        if spec is None:
            die("Spec-section autolink doesn't have a 'spec' attribute:\n{0}", outerHTML(el), el=el)
            continue
        if section is None:
            die("Spec-section autolink doesn't have a 'spec-section' attribute:\n{0}", outerHTML(el), el=el)
            continue
        if spec in doc.refs.headings:
            # Bikeshed recognizes the spec
            specData = doc.refs.headings[spec]
            if section in specData:
                heading = specData[section]
            else:
                die("Couldn't find section '{0}' in spec '{1}':\n{2}", section, spec, outerHTML(el), el=el)
                continue
            if isinstance(heading, list):
                # Multipage spec
                if len(heading) == 1:
                    # only one heading of this name, no worries
                    heading = specData[heading[0]]
                else:
                    # multiple headings of this id, user needs to disambiguate
                    die("Multiple headings with id '{0}' for spec '{1}'. Please specify:\n{2}", section, spec, "\n".join("  [[{0}]]".format(spec + x) for x in heading), el=el)
                    continue
            if doc.md.status == "current":
                if "current" in heading:
                    heading = heading["current"]
                else:
                    heading = heading["snapshot"]
            else:
                if "snapshot" in heading:
                    heading = heading["snapshot"]
                else:
                    heading = heading["current"]
            el.tag = "a"
            el.set("href", heading['url'])
            if isEmpty(el):
                el.text = "{spec} §{number} {text}".format(**heading)
        elif doc.refs.getBiblioRef(spec):
            # Bikeshed doesn't know the spec, but it's in biblio
            bib = doc.refs.getBiblioRef(spec)
            if isinstance(bib, biblio.StringBiblioEntry):
                die("Can't generate a cross-spec section ref for '{0}', because the biblio entry has no url.", spec, el=el)
                continue
            el.tag = "a"
            el.set("href", bib.url + section)
            if isEmpty(el):
                el.text = bib.title + " §" + section[1:]
        else:
            # Unknown spec
            die("Spec-section autolink tried to link to non-existent '{0}' spec:\n{1}", spec, outerHTML(el), el=el)
            continue
        removeAttr(el, 'data-link-spec')
        removeAttr(el, 'spec-section')


def fillAttributeInfoSpans(doc):
    # Auto-add <span attribute-info> to <dt><dfn> when it's an attribute or dict-member.
    for dt in findAll("dt", doc):
        if find("span[data-attribute-info]", dt) is not None:
            # Already has one, no need to do any work here
            continue
        dfn = find("dfn", dt)
        if dfn is None:
            continue
        dfnType = dfn.get("data-dfn-type")
        if dfnType == "attribute":
            attrName = "data-attribute-info"
        elif dfnType == "dict-member":
            attrName = "data-dict-member-info"
        else:
            continue
        spanFor = config.firstLinkTextFromElement(dfn)
        if spanFor is None:
            continue
        # Internal slots (denoted by [[foo]] naming scheme) don't have attribute info
        if spanFor.startswith("[["):
            continue
        if dfn.get("data-dfn-for"):
            spanFor = dfn.get("data-dfn-for") + "/" + spanFor
        insertAfter(dfn,
                    ", ",
                    E.span({attrName:"", "for":spanFor}))

    for el in findAll("span[data-attribute-info], span[data-dict-member-info]", doc):
        if el.get('data-attribute-info') is not None:
            refType = "attribute"
        else:
            refType = "dict-member"
        if (el.text is None or el.text.strip() == '') and len(el) == 0:
            referencedAttribute = el.get("for")
            if referencedAttribute is None or referencedAttribute == "":
                die("Missing for reference in attribute info span.", el=el)
                continue
            if "/" in referencedAttribute:
                interface, referencedAttribute = referencedAttribute.split("/")
                target = findAll('a[data-link-type={2}][data-lt="{0}"][data-link-for="{1}"]'.format(referencedAttribute, interface, refType), doc)
            else:
                target = findAll('a[data-link-type={1}][data-lt="{0}"]'.format(referencedAttribute, refType), doc)
            if len(target) == 0:
                die("Couldn't find target {1} '{0}':\n{2}", referencedAttribute, refType, outerHTML(el), el=el)
                continue
            elif len(target) > 1:
                die("Multiple potential target {1}s '{0}':\n{2}", referencedAttribute, refType, outerHTML(el), el=el)
                continue
            target = target[0]
            datatype = target.get("data-type").strip()
            default = target.get("data-default")
            decorations = []
            if target.get("data-readonly") is not None:
                decorations.append(", readonly")
            if datatype[-1] == "?":
                decorations.append(", nullable")
                datatype = datatype[:-1]
            if default is not None:
                decorations.append(", defaulting to ")
                decorations.append(E.code(default))
            if datatype[0] == "(":
                # Union type
                # TODO(Nov 2015): actually handle this properly, don't have time to think through it right now.
                appendChild(el,
                            " of type ",
                            E.code({"class":"idl-code"}, datatype),
                            *decorations)
            elif re.match(r"(\w+)<(\w+)>", datatype):
                # Sequence type
                match = re.match(r"(\w+)<(\w+)>", datatype)
                appendChild(el,
                            " of type ",
                            match.group(1),
                            "<",
                            E.a({"data-link-type":"idl-name"}, match.group(2)),
                            ">",
                            *decorations)
            else:
                # Everything else
                appendChild(el,
                            " of type ",
                            E.a({"data-link-type":"idl-name"}, datatype),
                            *decorations)


def processDfns(doc):
    dfns = findAll(config.dfnElementsSelector, doc)
    classifyDfns(doc, dfns)
    fixupIDs(doc, dfns)
    doc.refs.addLocalDfns(dfn for dfn in dfns if dfn.get('id') is not None)


def determineDfnType(dfn, inferCSS=False):
    # 1. Look at data-dfn-type
    if dfn.get('data-dfn-type'):
        return dfn.get('data-dfn-type')
    # 2. Look for a prefix on the id
    if dfn.get('id'):
        id = dfn.get('id')
        for prefix, type in config.dfnClassToType.items():
            if id.startswith(prefix):
                return type
    # 3. Look for a class or data-dfn-type on the ancestors
    for ancestor in dfn.iterancestors():
        if ancestor.get('data-dfn-type'):
            return ancestor.get('data-dfn-type')
        for cls, type in config.dfnClassToType.items():
            if hasClass(ancestor, cls):
                return type
            if hasClass(ancestor, "idl") and not hasClass(ancestor, "extract"):
                return "interface"
    # 4. Introspect on the text
    if inferCSS:
        text = textContent(dfn)
        if text[0:1] == "@":
            return "at-rule"
        elif len(dfn) == 1 and dfn[0].get('data-link-type') == "maybe" and emptyText(dfn.text) and emptyText(dfn[0].tail):
            return "value"
        elif text[0:1] == "<" and text[-1:] == ">":
            return "type"
        elif text[0:1] == ":":
            return "selector"
        elif re.match(r"^[\w-]+\(.*\)$", text) and not (dfn.get('id') or '').startswith("dom-"):
            return "function"
    # 5. Assume it's a "dfn"
    return "dfn"


def classifyDfns(doc, dfns):
    dfnTypeToPrefix = {v:k for k,v in config.dfnClassToType.items()}
    for el in dfns:
        dfnType = determineDfnType(el, inferCSS=doc.md.inferCSSDfns)
        if dfnType not in config.dfnTypes:
            die("Unknown dfn type '{0}' on:\n{1}", dfnType, outerHTML(el), el=el)
            continue
        dfnFor = treeAttr(el, "data-dfn-for")
        primaryDfnText = config.firstLinkTextFromElement(el)
        if primaryDfnText is None:
            die("Dfn has no linking text:\n{0}", outerHTML(el), el=el)
            continue
        # Check for invalid fors, as it's usually some misnesting.
        if dfnFor and dfnType in config.typesNotUsingFor:
            die("'{0}' definitions don't use a 'for' attribute, but this one claims it's for '{1}' (perhaps inherited from an ancestor). This is probably a markup error.\n{2}", dfnType, dfnFor, outerHTML(el), el=el)
        # Push the dfn type down to the <dfn> itself.
        if el.get('data-dfn-type') is None:
            el.set('data-dfn-type', dfnType)
        # Push the for value too.
        if dfnFor:
            el.set('data-dfn-for', dfnFor)
        elif dfnType in config.typesUsingFor:
            die("'{0}' definitions need to specify what they're for.\nAdd a 'for' attribute to {1}, or add 'dfn-for' to an ancestor.", dfnType, outerHTML(el), el=el)
            continue
        # Some error checking
        if dfnType in config.functionishTypes:
            if not re.match(r"^[\w\[\]-]+\(.*\)$", primaryDfnText):
                die("Functions/methods must end with () in their linking text, got '{0}'.", primaryDfnText, el=el)
                continue
            elif el.get('data-lt') is None:
                if dfnType == "function":
                    # CSS function, define it with no args in the text
                    primaryDfnText = re.match(r"^([\w\[\]-]+)\(.*\)$", primaryDfnText).group(1) + "()"
                    el.set('data-lt', primaryDfnText)
                elif dfnType in config.idlTypes:
                    # IDL methodish construct, ask the widlparser what it should have.
                    # If the method isn't in any IDL, this tries its best to normalize it anyway.
                    names = list(doc.widl.normalizedMethodNames(primaryDfnText, el.get('data-dfn-for')))
                    primaryDfnText = names[0]
                    el.set('data-lt', "|".join(names))
                else:
                    die("BIKESHED ERROR: Unhandled functionish type '{0}' in classifyDfns. Please report this to Bikeshed's maintainer.", dfnType, el=el)
        # If type=argument, try to infer what it's for.
        if dfnType == "argument" and el.get('data-dfn-for') is None:
            parent = el.getparent()
            parentFor = parent.get('data-dfn-for')
            if parent.get('data-dfn-type') in config.functionishTypes and parentFor is not None:
                dfnFor = ", ".join(parentFor + "/" + name for name in doc.widl.normalizedMethodNames(textContent(parent), parentFor))
            elif treeAttr(el, "data-dfn-for") is None:
                die("'argument' dfns need to specify what they're for, or have it be inferrable from their parent. Got:\n{0}", outerHTML(el), el=el)
                continue
        # Automatically fill in id if necessary.
        if el.get('id') is None:
            if dfnFor:
                singleFor = config.splitForValues(dfnFor)[0]
            if dfnType in config.functionishTypes.intersection(config.idlTypes):
                id = config.simplifyText("{_for}-{id}".format(_for=singleFor, id=re.match(r"[^(]*", primaryDfnText).group(0) + "()"))
                el.set("data-alternate-id", config.simplifyText("dom-{_for}-{id}".format(_for=singleFor, id=primaryDfnText)))
                if primaryDfnText.startswith("[["):
                    # Slots get their identifying [] stripped from their ID,
                    # so gotta dedup them some other way.
                    id += "-slot"
                    el.set("data-alternate-id", "{0}-slot".format(el.get("data-alternate-id")))
            else:
                if dfnFor:
                    id = config.simplifyText("{_for}-{id}".format(_for=singleFor, id=primaryDfnText))
                else:
                    id = config.simplifyText(primaryDfnText)
            if dfnType == "dfn":
                pass
            elif dfnType == "interface":
                pass
            elif dfnType == "event":
                # Special case 'event' because it needs a different format from IDL types
                id = config.simplifyText("{type}-{id}".format(type=dfnTypeToPrefix[dfnType], _for=singleFor, id=id))
            elif dfnType == "attribute" and primaryDfnText.startswith("[["):
                # Slots get their identifying [] stripped from their ID, so gotta dedup them some other way.
                id = config.simplifyText("dom-{id}-slot".format(_for=singleFor, id=id))
            elif dfnType in config.idlTypes.intersection(config.typesUsingFor):
                id = config.simplifyText("dom-{id}".format(id=id))
            else:
                id = "{type}-{id}".format(type=dfnTypeToPrefix[dfnType], id=id)
            el.set('id', id)
        # Set lt if it's not set,
        # and doing so won't mess with anything else.
        if el.get('data-lt') is None and "|" not in primaryDfnText:
            el.set('data-lt', primaryDfnText)
        # Push export/noexport down to the definition
        if el.get('data-export') is None and el.get('data-noexport') is None:
            for ancestor in el.iterancestors():
                if ancestor.get('data-export') is not None:
                    el.set('data-export', '')
                    break
                elif ancestor.get('data-noexport') is not None:
                    el.set('data-noexport', '')
                    break
            else:
                if dfnType == "dfn":
                    el.set('data-noexport', '')
                else:
                    el.set('data-export', '')


def determineLinkType(el):
    # 1. Look at data-link-type
    linkType = treeAttr(el, 'data-link-type')
    if linkType:
        if linkType in config.linkTypes:
            return linkType
        die("Unknown link type '{0}' on:\n{1}", linkType, outerHTML(el), el=el)
        return "unknown-type"
    # 2. Introspect on the text
    text = textContent(el)
    if config.typeRe["at-rule"].match(text):
        return "at-rule"
    elif config.typeRe["type"].match(text):
        return "type"
    elif config.typeRe["selector"].match(text):
        return "selector"
    elif config.typeRe["function"].match(text):
        return "functionish"
    else:
        return "dfn"


def determineLinkText(el):
    linkType = el.get('data-link-type')
    contents = textContent(el)
    if el.get('data-lt'):
        linkText = el.get('data-lt')
    elif linkType in config.functionishTypes.union(["functionish"]) and re.match(r"^[\w-]+\(.*\)$", contents):
        linkText = re.match(r"^([\w-]+)\(.*\)$", contents).group(1) + "()"
        # Need to fix this using the idl parser.
    else:
        linkText = contents
    linkText = foldWhitespace(linkText)
    if len(linkText) == 0:
        die("Autolink {0} has no linktext.", outerHTML(el), el=el)
    return linkText


def classifyLink(el):
    linkType = determineLinkType(el)
    el.set('data-link-type', linkType)
    linkText = determineLinkText(el)

    el.set('data-lt', linkText)
    for attr in ["data-link-status", "data-link-for", "data-link-spec", "data-link-for-hint"]:
        val = treeAttr(el, attr)
        if val is not None:
            el.set(attr, val)
    return el

# Additional Processing


def processBiblioLinks(doc):
    biblioLinks = findAll("a[data-link-type='biblio']", doc)
    for el in biblioLinks:
        biblioType = el.get('data-biblio-type')
        if biblioType == "normative":
            storage = doc.normativeRefs
        elif biblioType == "informative":
            storage = doc.informativeRefs
        else:
            die("Unknown data-biblio-type value '{0}' on {1}. Only 'normative' and 'informative' allowed.", biblioType, outerHTML(el), el=el)
            continue

        linkText = determineLinkText(el)
        if linkText[0] == "[" and linkText[-1] == "]":
            linkText = linkText[1:-1]

        refStatus = treeAttr(el, "data-biblio-status") or doc.md.defaultRefStatus

        okayToFail = el.get('data-okay-to-fail') is not None

        ref = doc.refs.getBiblioRef(linkText, status=refStatus, generateFakeRef=okayToFail, el=el)
        if not ref:
            if not okayToFail:
                closeBiblios = biblio.findCloseBiblios(doc.refs.biblioKeys, linkText)
                die("Couldn't find '{0}' in bibliography data. Did you mean:\n{1}", linkText, '\n'.join("  " + b for b in closeBiblios), el=el)
            el.tag = "span"
            continue

        # Need to register that I have a preferred way to refer to this biblio,
        # in case aliases show up - they all need to use my preferred key!
        if hasattr(ref, "originalLinkText"):
            # Okay, so this particular ref has been reffed before...
            if linkText == ref.linkText:
                # Whew, and with the same name I'm using now. Ship it.
                pass
            else:
                # Oh no! I'm using two different names to refer to the same biblio!
                die("The biblio refs [[{0}]] and [[{1}]] are both aliases of the same base reference [[{2}]]. Please choose one name and use it consistently.", linkText, ref.linkText, ref.originalLinkText, el=el)
                # I can keep going, tho - no need to skip this ref
        else:
            # This is the first time I've reffed this particular biblio.
            # Register this as the preferred name...
            doc.refs.preferredBiblioNames[ref.linkText] = linkText
            # Use it on the current ref. Future ones will use the preferred name automatically.
            ref.linkText = linkText

        id = config.simplifyText(ref.linkText)
        el.set('href', '#biblio-' + id)
        storage[ref.linkText] = ref


def processAutolinks(doc):
    # An <a> without an href is an autolink.
    # <i> is a legacy syntax for term autolinks. If it links up, we change it into an <a>.
    # We exclude bibliographical links, as those are processed in `processBiblioLinks`.
    query = "a:not([href]):not([data-link-type='biblio'])"
    if doc.md.useIAutolinks:
        query += ", i"
    autolinks = findAll(query, doc)
    for el in autolinks:
        # Explicitly empty linking text indicates this shouldn't be an autolink.
        if el.get('data-lt') == '':
            continue

        classifyLink(el)
        linkType = el.get('data-link-type')
        linkText = el.get('data-lt')

        # Properties and descriptors are often written like 'foo-*'. Just ignore these.
        if linkType in ("property", "descriptor", "propdesc") and "*" in linkText:
            continue

        # Not super clear why I think links will specify multiple for values,
        # or why it's okay to just use the first one in that case.
        linkFor = config.splitForValues(el.get('data-link-for'))
        if linkFor:
            linkFor = linkFor[0]
        if not linkFor and doc.md.assumeExplicitFor:
            linkFor = "/"

        # Status used to use ED/TR, so convert those if they appear,
        # and verify
        status = el.get('data-link-status')
        if status == "ED":
            status = "current"
        elif status == "TR":
            status = "snapshot"
        elif status in config.linkStatuses or status is None:
            pass
        else:
            die("Unknown link status '{0}' on {1}", status, outerHTML(el))
            continue

        ref = doc.refs.getRef(linkType, linkText,
                              spec=el.get('data-link-spec'),
                              status=status,
                              linkFor=linkFor,
                              linkForHint=el.get('data-link-for-hint'),
                              el=el,
                              error=(linkText.lower() not in doc.md.ignoredTerms))
        # Capture the reference (and ensure we add a biblio entry) if it
        # points to an external specification. We check the spec name here
        # rather than checking `status == "local"`, as "local" refs include
        # those defined in `<pre class="anchor">` datablocks, which we do
        # want to capture here.
        if ref and ref.spec and ref.spec.lower() != doc.refs.spec.lower():
            spec = ref.spec.lower()
            key = ref.for_[0] if ref.for_ else ""
            doc.externalRefsUsed[spec][ref.text][key] = ref
            if isNormative(el):
                biblioStorage = doc.normativeRefs
            else:
                biblioStorage = doc.informativeRefs
            biblioRef = doc.refs.getBiblioRef(ref.spec, generateFakeRef=True)
            if biblioRef:
                biblioStorage[biblioRef.linkText] = biblioRef

        if ref:
            el.set('href', ref.url)
            el.tag = "a"
            decorateAutolink(doc, el, linkType=linkType, linkText=linkText)
        else:
            if linkType == "maybe":
                el.tag = "css"
                if el.get("data-link-type"):
                    del el.attrib["data-link-type"]
                if el.get("data-lt"):
                    del el.attrib["data-lt"]


def decorateAutolink(doc, el, linkType, linkText):
    # Add additional effects to some autolinks.
    if linkType == "type":
        # Get all the values that the type expands to, add it as a title.
        titleText = None
        if linkText in doc.typeExpansions:
            titleText = doc.typeExpansions[linkText]
        else:
            refs = doc.refs.queryAllRefs(linkFor=linkText, ignoreObsoletes=True)
            if refs:
                titleText = "Expands to: " + ' | '.join({ref.text for ref in refs})
                doc.typeExpansions[linkText] = titleText
        if titleText:
            el.set('title', titleText)


def processIssuesAndExamples(doc):
    # Add an auto-genned and stable-against-changes-elsewhere id to all issues and
    # examples, and link to remote issues if possible:
    for el in findAll(".issue:not([id])", doc):
        el.set('id', "issue-" + hashContents(el))
        remoteIssueID = el.get('data-remote-issue-id')
        if remoteIssueID:
            del el.attrib['data-remote-issue-id']
            # Eventually need to support a way to trigger other repo url structures,
            # but defaulting to GH is fine for now.
            githubMatch = re.match(r"\s*([\w-]+)/([\w-]+)#(\d+)\s*$", remoteIssueID)
            numberMatch = re.match(r"\s*(\d+)\s*$", remoteIssueID)
            remoteIssueURL = None
            if githubMatch:
                remoteIssueURL = "https://github.com/{0}/{1}/issues/{2}".format(*githubMatch.groups())
                if doc.md.inlineGithubIssues:
                    el.set("data-inline-github", "{0} {1} {2}".format(*githubMatch.groups()))
            elif numberMatch and doc.md.repository.type == "github":
                remoteIssueURL = doc.md.repository.formatIssueUrl(numberMatch.group(1))
                if doc.md.inlineGithubIssues:
                    el.set("data-inline-github", "{0} {1} {2}".format(doc.md.repository.user, doc.md.repository.repo, numberMatch.group(1)))
            elif doc.md.issueTrackerTemplate:
                remoteIssueURL = doc.md.issueTrackerTemplate.format(remoteIssueID)
            if remoteIssueURL:
                appendChild(el, " ", E.a({"href": remoteIssueURL}, "<" + remoteIssueURL + ">"))
    for el in findAll(".example:not([id])", doc):
        el.set('id', "example-" + hashContents(el))
    fixupIDs(doc, findAll(".issue, .example", doc))


def addSelfLinks(doc):
    def makeSelfLink(el):
        return E.a({"href": "#" + urllib.quote(el.get('id', '')), "class":"self-link"})

    dfnElements = findAll(config.dfnElementsSelector, doc)

    foundFirstNumberedSection = False
    for el in findAll("h2, h3, h4, h5, h6", doc):
        foundFirstNumberedSection = foundFirstNumberedSection or (el.get('data-level') is not None)
        if el in dfnElements:
            # It'll get a self-link or dfn-panel later.
            continue
        if foundFirstNumberedSection:
            appendChild(el, makeSelfLink(el))
    for el in findAll(".issue[id], .example[id], .note[id], li[id], dt[id]", doc):
        if list(el.iterancestors("figure")):
            # Skipping - element is inside a figure and is part of an example.
            continue
        if el.get("data-no-self-link") is not None:
            continue
        prependChild(el, makeSelfLink(el))
    if doc.md.useDfnPanels:
        addDfnPanels(doc, dfnElements)
    else:
        for el in dfnElements:
            if list(el.iterancestors("a")):
                warn("Found <a> ancestor, skipping self-link. Swap <dfn>/<a> order?\n  {0}", outerHTML(el), el=el)
                continue
            appendChild(el, makeSelfLink(el))


def addDfnPanels(doc, dfns):
    from .DefaultOrderedDict import DefaultOrderedDict
    # Constructs "dfn panels" which show all the local references to a term
    atLeastOnePanel = False
    # Gather all the <a href>s together
    allRefs = DefaultOrderedDict(list)
    for a in findAll("a", doc):
        href = a.get("href")
        if href is None:
            continue
        if not href.startswith("#"):
            continue
        allRefs[href[1:]].append(a)
    body = find("body", doc)
    for dfn in dfns:
        id = dfn.get("id")
        if not id:
            # Something went wrong, bail.
            continue
        refs = DefaultOrderedDict(list)
        for link in allRefs[id]:
            h = relevantHeadings(link).next()
            if hasClass(h, "no-ref"):
                continue
            sectionText = textContent(h)
            refs[sectionText].append(link)
        if not refs:
            # Just insert a self-link instead
            appendChild(dfn,
                        E.a({"href": "#" + urllib.quote(id), "class":"self-link"}))
            continue
        addClass(dfn, "dfn-paneled")
        atLeastOnePanel = True
        panel = E.aside({"class": "dfn-panel", "data-for": id},
                        E.b(
            E.a({"href":"#" + urllib.quote(id)}, "#" + id)),
            E.b("Referenced in:"))
        counter = 0
        ul = appendChild(panel, E.ul())
        for text,els in refs.items():
            li = appendChild(ul, E.li())
            for i,el in enumerate(els):
                counter += 1
                refID = el.get("id")
                if refID is None:
                    refID = "ref-for-{0}-{1}".format(id, counter)
                    el.set("id", refID)
                if i == 0:
                    appendChild(li,
                                E.a({"href": "#" + urllib.quote(refID)}, text))
                else:
                    appendChild(li,
                                " ",
                                E.a({"href": "#" + urllib.quote(refID)}, "(" + str(i + 1) + ")"))
        appendChild(body, panel)
    if atLeastOnePanel:
        doc.extraScripts['script-dfn-panel'] = '''
        document.body.addEventListener("click", function(e) {
            var queryAll = function(sel) { return [].slice.call(document.querySelectorAll(sel)); }
            // Find the dfn element or panel, if any, that was clicked on.
            var el = e.target;
            var target;
            var hitALink = false;
            while(el.parentElement) {
                if(el.tagName == "A") {
                    // Clicking on a link in a <dfn> shouldn't summon the panel
                    hitALink = true;
                }
                if(el.classList.contains("dfn-paneled")) {
                    target = "dfn";
                    break;
                }
                if(el.classList.contains("dfn-panel")) {
                    target = "dfn-panel";
                    break;
                }
                el = el.parentElement;
            }
            if(target != "dfn-panel") {
                // Turn off any currently "on" or "activated" panels.
                queryAll(".dfn-panel.on, .dfn-panel.activated").forEach(function(el){
                    el.classList.remove("on");
                    el.classList.remove("activated");
                });
            }
            if(target == "dfn" && !hitALink) {
                // open the panel
                var dfnPanel = document.querySelector(".dfn-panel[data-for='" + el.id + "']");
                if(dfnPanel) {
                    console.log(dfnPanel);
                    dfnPanel.classList.add("on");
                    var rect = el.getBoundingClientRect();
                    dfnPanel.style.left = window.scrollX + rect.right + 5 + "px";
                    dfnPanel.style.top = window.scrollY + rect.top + "px";
                    var panelRect = dfnPanel.getBoundingClientRect();
                    var panelWidth = panelRect.right - panelRect.left;
                    if(panelRect.right > document.body.scrollWidth && (rect.left - (panelWidth + 5)) > 0) {
                        // Reposition, because the panel is overflowing
                        dfnPanel.style.left = window.scrollX + rect.left - (panelWidth + 5) + "px";
                    }
                } else {
                    console.log("Couldn't find .dfn-panel[data-for='" + el.id + "']");
                }
            } else if(target == "dfn-panel") {
                // Switch it to "activated" state, which pins it.
                el.classList.add("activated");
                el.style.left = null;
                el.style.top = null;
            }

        });
        '''
        doc.extraStyles['style-dfn-panel'] = '''
        .dfn-panel {
            position: absolute;
            z-index: 35;
            height: auto;
            width: -webkit-fit-content;
            width: fit-content;
            max-width: 300px;
            max-height: 500px;
            overflow: auto;
            padding: 0.5em 0.75em;
            font: small Helvetica Neue, sans-serif, Droid Sans Fallback;
            background: #DDDDDD;
            color: black;
            border: outset 0.2em;
        }
        .dfn-panel:not(.on) { display: none; }
        .dfn-panel * { margin: 0; padding: 0; text-indent: 0; }
        .dfn-panel > b { display: block; }
        .dfn-panel a { color: black; }
        .dfn-panel a:not(:hover) { text-decoration: none !important; border-bottom: none !important; }
        .dfn-panel > b + b { margin-top: 0.25em; }
        .dfn-panel ul { padding: 0; }
        .dfn-panel li { list-style: inside; }
        .dfn-panel.activated {
            display: inline-block;
            position: fixed;
            left: .5em;
            bottom: 2em;
            margin: 0 auto;
            max-width: calc(100vw - 1.5em - .4em - .5em);
            max-height: 30vh;
        }

        .dfn-paneled { cursor: pointer; }
        '''


class DebugMarker(object):
    # Debugging tool for IDL markup

    def markupConstruct(self, text, construct):
        return ('<construct-' + construct.idlType + '>', '</construct-' + construct.idlType + '>')

    def markupType(self, text, construct):
        return ('<TYPE for="' + construct.idlType + '" idlType="' + text + '">', '</TYPE>')

    def markupPrimitiveType(self, text, construct):
        return ('<PRIMITIVE for="' + construct.idlType + '" idlType="' + text + '">', '</PRIMITIVE>')

    def markupBufferType(self, text, construct):
        return ('<BUFFER for="' + construct.idlType + '" idlType="' + text + '">', '</BUFFER>')

    def markupStringType(self, text, construct):
        return ('<STRING for="' + construct.idlType + '" idlType="' + text + '">', '</STRING>')

    def markupObjectType(self, text, construct):
        return ('<OBJECT for="' + construct.idlType + '" idlType="' + text + '">', '</OBJECT>')

    def markupTypeName(self, text, construct):
        return ('<TYPE-NAME idlType="' + construct.idlType + '">', '</TYPE-NAME>')

    def markupName(self, text, construct):
        return ('<NAME idlType="' + construct.idlType + '">', '</NAME>')

    def markupKeyword(self, text, construct):
        return ('<KEYWORD idlType="' + construct.idlType + '">', '</KEYWORD>')

    def markupEnumValue(self, text, construct):
        return ('<ENUM-VALUE for="' + construct.name + '">', '</ENUM-VALUE>')


class IDLMarker(object):
    def markupConstruct(self, text, construct):
        # Fires for every 'construct' in the WebIDL.
        # Some things are "productions", not "constructs".
        return (None, None)

    def markupType(self, text, construct):
        # Fires for entire type definitions.
        # It'll contain keywords or names, or sometimes more types.
        # For example, a "type" wrapper surrounds an entire union type,
        # as well as its component types.
        return (None, None)

    def markupPrimitiveType(self, text, construct):
        return ("<a class=n data-link-type=interface>", "</a>")

    def markupStringType(self, text, construct):
        return ("<a class=n data-link-type=interface>", "</a>")

    def markupBufferType(self, text, construct):
        return ("<a class=n data-link-type=interface>", "</a>")

    def markupObjectType(self, text, construct):
        return ("<a class=n data-link-type=interface>", "</a>")

    def markupTypeName(self, text, construct):
        # Fires for non-defining type names, such as arg types.

        # The names in [Exposed=Foo] are [Global] tokens, not interface names.
        # Since I don't track globals as a link target yet, don't link them at all.
        if construct.idlType == "extended-attribute" and construct.name == "Exposed":
            return ("<span class=n>", "</span>")

        # The name in [PutForwards=foo] is an attribute of the same interface.
        if construct.idlType == "extended-attribute" and construct.name == "PutForwards":
            # In [PutForwards=value] attribute DOMString foo
            # the "value" is a DOMString attr
            attr = construct.parent
            if hasattr(attr.member, "rest"):
                type = attr.member.rest.type
            elif hasattr(attr.member, "attribute"):
                type = attr.member.attribute.type
            typeName = str(type).strip()
            if typeName.endswith("?"):
                typeName = typeName[:-1]
            return ('<a class=n data-link-type=attribute data-link-for="{0}">'.format(typeName), '</a>')

        if construct.idlType == "constructor":
            # This shows up for the method name in a [NamedConstructor] extended attribute.
            # The "NamedConstructor" Name already got markup up, so ignore this one.
            return ("<span class=n>", "</span>")

        return ('<a class=n data-link-type="idl-name">', '</a>')

    def markupKeyword(self, text, construct):
        # Fires on the various "keywords" of WebIDL -
        # words that are part of the WebIDL syntax,
        # rather than names exposed to JS.
        # Examples: "interface", "stringifier", the IDL-defined type names like "DOMString" and "long".
        if text == "stringifier":
            if construct.name is None:
                # If no name was defined, you're required to define stringification behavior.
                return ("<a class=kt dfn for='{0}' data-lt='stringification behavior'>".format(construct.parent.fullName), "</a>")
            else:
                # Otherwise, you *can* point to/dfn stringification behavior if you want.
                return ("<idl class=kt data-idl-type=dfn data-idl-for='{0}' data-lt='stringification behavior' id='{0}-stringification-behavior'>".format(construct.parent.fullName), "</idl>")
        return ("<span class=kt>", "</span>")

    def markupName(self, text, construct):
        # Fires for defining names: method names, arg names, interface names, etc.
        if construct.idlType not in config.idlTypes:
            return ("<span class=nv>", "</span>")

        idlType = construct.idlType
        extraParameters = ''
        idlTitle = construct.normalName
        refType = "idl"
        if idlType in config.functionishTypes:
            idlTitle = '|'.join(self.methodLinkingTexts(construct))
        elif idlType == "extended-attribute":
            refType = "link"
        elif idlType == "attribute":
            if hasattr(construct.member, "rest"):
                rest = construct.member.rest
            elif hasattr(construct.member, "attribute"):
                rest = construct.member.attribute
            else:
                die("Can't figure out how to construct attribute-info from:\n  {0}", construct)
            if rest.readonly is not None:
                readonly = 'data-readonly'
            else:
                readonly = ''
            extraParameters = '{0} data-type="{1}"'.format(readonly, unicode(rest.type).strip())
        elif idlType == "dict-member":
            extraParameters = 'data-type="{0}"'.format(construct.type)
            if construct.default is not None:
                value = escapeAttr("{0}".format(construct.default.value))
                extraParameters += ' data-default="{0}"'.format(value)
        elif idlType in ["interface", "namespace"]:
            if construct.partial:
                refType = "link"

        if refType == "link":
            elementName = "a"
        else:
            elementName = "idl"

        if idlType in config.typesUsingFor:
            if idlType == "argument" and construct.parent.idlType == "method":
                interfaceName = construct.parent.parent.name
                methodNames = ["{0}/{1}".format(interfaceName, m) for m in construct.parent.methodNames]
                idlFor = "data-idl-for='{0}'".format(", ".join(methodNames))
            else:
                idlFor = "data-idl-for='{0}'".format(construct.parent.fullName)
        else:
            idlFor = ""
        return ('<{name} class=nv data-lt="{0}" data-{refType}-type="{1}" {2} {3}>'.format(idlTitle, idlType, idlFor, extraParameters, name=elementName, refType=refType), '</{0}>'.format(elementName))

    def markupEnumValue(self, text, construct):
        texts = [text, text.strip("\"")]
        lt = "|".join(escapeAttr(t) for t in texts)
        return ("<idl class=s data-idl-type=enum-value data-idl-for='{0}' data-lt='{1}'>".format(escapeAttr(construct.name), lt), "</idl>")

    def encode(self, text):
        return escapeHTML(text)

    def methodLinkingTexts(self, method):
        '''
        Given a method-ish widlparser Construct,
        finds all possible linking texts.
        The full linking text is "foo(bar, baz)";
        beyond that, any optional or variadic arguments can be omitted.
        So, if both were optional,
        "foo(bar)" and "foo()" would both also be valid linking texts.
        '''
        if getattr(method, "arguments", None) is None:
            return [method.normalName]
        for i,arg in enumerate(method.arguments):
            if arg.optional or arg.variadic:
                optStart = i
                break
        else:
            # No optionals, so no work to be done
            return [method.normalName]
        prefix = method.name + "("
        texts = []
        for i in range(optStart, len(method.arguments)):
            argText = ', '.join(arg.name for arg in method.arguments[:i])
            texts.append(prefix + argText + ")")
        texts.append(method.normalName)
        return reversed(texts)


class IDLUI(object):
    def warn(self, msg):
        die("{0}", msg.rstrip())

class IDLSilent(object):
    def warn(self, msg):
        pass


def markupIDL(doc):
    highlightingOccurred = False
    idlEls = findAll("pre.idl:not([data-no-idl]), xmp.idl:not([data-no-idl])", doc)
    # One pass with a silent parser to collect the symbol table.
    symbolTable = None
    for el in idlEls:
        p = parser.Parser(textContent(el), ui=IDLSilent(), symbolTable=symbolTable)
        symbolTable = p.symbolTable
    # Then a real pass to actually mark up the IDL,
    # and collect it for the index.
    for el in idlEls:
        if isNormative(el):
            text = textContent(el)
            # Parse once with a fresh parser, so I can spit out just this <pre>'s markup.
            widl = parser.Parser(text, ui=IDLUI(), symbolTable=symbolTable)
            marker = DebugMarker() if doc.debug else IDLMarker()
            replaceContents(el, parseHTML(unicode(widl.markup(marker))))
            # Parse a second time with the global one, which collects all data in the doc.
            doc.widl.parse(text)
        addClass(el, "highlight")
        highlightingOccurred = True
    if highlightingOccurred:
        doc.extraStyles['style-syntax-highlighting'] += "pre.idl.highlight { color: #708090; }"


def processIDL(doc):
    for pre in findAll("pre.idl, xmp.idl", doc):
        if pre.get("data-no-idl") is not None:
            continue
        if not isNormative(pre):
            continue
        forcedInterfaces = []
        for x in (treeAttr(pre, "data-dfn-force") or "").split():
            x = x.strip()
            if x.endswith("<interface>"):
                x = x[:-11]
            forcedInterfaces.append(x)
        for el in findAll("idl", pre):
            idlType = el.get('data-idl-type')
            url = None
            forceDfn = False
            ref = None
            for idlText in el.get('data-lt').split('|'):
                if idlType == "interface" and idlText in forcedInterfaces:
                    forceDfn = True
                for linkFor in config.splitForValues(el.get('data-idl-for', '')) or [None]:
                    ref = doc.refs.getRef(idlType, idlText,
                                          linkFor=linkFor,
                                          status="local",
                                          el=el,
                                          error=False)
                    if ref:
                        url = ref.url
                        break
                if ref:
                    break
            if url is None or forceDfn:
                el.tag = "dfn"
                el.set('data-dfn-type', idlType)
                del el.attrib['data-idl-type']
                if el.get('data-idl-for'):
                    el.set('data-dfn-for', el.get('data-idl-for'))
                    del el.attrib['data-idl-for']
            else:
                el.tag = "a"
                el.set('data-link-type', idlType)
                el.set('data-lt', idlText)
                del el.attrib['data-idl-type']
                if el.get('data-idl-for'):
                    el.set('data-link-for', el.get('data-idl-for'))
                    del el.attrib['data-idl-for']
                if el.get('id'):
                    # ID was defensively added by the Marker.
                    del el.attrib['id']
    dfns = findAll("pre.idl:not([data-no-idl]) dfn, xmp.idl:not([data-no-idl]) dfn", doc)
    classifyDfns(doc, dfns)
    fixupIDs(doc, dfns)
    doc.refs.addLocalDfns(dfn for dfn in dfns if dfn.get('id') is not None)






def cleanupHTML(doc):
    # Cleanup done immediately before serialization.

    head = None
    inBody = False
    strayHeadEls = []
    styleScoped = []
    nestedLists = []
    flattenEls = []
    for el in doc.document.iter():
        if head is None and el.tag == "head":
            head = el
            continue
        if el.tag == "body":
            inBody = True

        # Move any stray <link>, <meta>, or <style> into the <head>.
        if inBody and el.tag in ["link", "meta", "style"]:
            strayHeadEls.append(el)

        if el.tag == "style" and el.get("scoped") is not None:
            die("<style scoped> is no longer part of HTML. Ensure your styles can apply document-globally and remove the scoped attribute.", el=el)
            styleScoped.append(el)

        # Convert the technically-invalid <nobr> element to an appropriate <span>
        if el.tag == "nobr":
            el.tag == "span"
            el.set("style", el.get('style', '') + ";white-space:nowrap")

        # And convert <xmp> to <pre>
        if el.tag == "xmp":
            el.tag = "pre"

        # If we accidentally recognized an autolink shortcut in SVG, kill it.
        if el.tag == "{http://www.w3.org/2000/svg}a" and el.get("data-link-type") is not None:
            removeAttr(el, "data-link-type")
            el.tag = "{http://www.w3.org/2000/svg}tspan"

        # Add .algorithm to [algorithm] elements, for styling
        if el.get("data-algorithm") is not None and not hasClass(el, "algorithm"):
            addClass(el, "algorithm")

        # Allow MD-generated lists to be surrounded by HTML list containers,
        # so you can add classes/etc without an extraneous wrapper.
        if el.tag in ["ol", "ul", "dl"]:
            onlyChild = hasOnlyChild(el)
            if onlyChild is not None and el.tag == onlyChild.tag and el.get("data-md") is None and onlyChild.get("data-md") is not None:
                # The md-generated list container is featureless,
                # so we can just throw it away and move its children into its parent.
                nestedLists.append(onlyChild)
            else:
                # Remove any lingering data-md attributes on lists that weren't using this container replacement thing.
                removeAttr(el, "data-md")

        # Mark pre.idl blocks as .def, for styling
        if el.tag == "pre" and hasClass(el, "idl") and not hasClass(el, "def"):
            addClass(el, "def")

        # Tag classes on wide types of dfns/links
        if el.tag in config.dfnElements:
            if el.get("data-dfn-type") in config.idlTypes:
                addClass(el, "idl-code")
            if el.get("data-dfn-type") in config.maybeTypes.union(config.linkTypeToDfnType['propdesc']):
                if not hasAncestor(el, lambda x:x.tag=="pre"):
                    addClass(el, "css")
        if el.tag == "a":
            if el.get("data-link-type") in config.idlTypes:
                addClass(el, "idl-code")
            if el.get("data-link-type") in config.maybeTypes.union(config.linkTypeToDfnType['propdesc']):
                if not hasAncestor(el, lambda x:x.tag=="pre"):
                    addClass(el, "css")

        # Remove duplicate linking texts.
        if el.tag in config.anchorishElements and el.get("data-lt") is not None and el.get("data-lt") == textContent(el, exact=True):
            removeAttr(el, "data-lt")

        # Transform the <css> fake tag into markup.
        # (Used when the ''foo'' shorthand doesn't work.)
        if el.tag == "css":
            el.tag = "span"
            addClass(el, "css")

        # Transform the <assert> fake tag into a span with a unique ID based on its contents.
        # This is just used to tag arbitrary sections with an ID so you can point tests at it.
        # (And the ID will be guaranteed stable across publications, but guaranteed to change when the text changes.)
        if el.tag == "assert":
            el.tag = "span"
            el.set("id", "assert-" + hashContents(el))

        # Add ARIA role of "note" to class="note" elements
        if el.tag in ["div", "p"] and hasClass(el, doc.md.noteClass):
            el.set("role", "note")

        # Look for nested <a> elements, and warn about them.
        if el.tag == "a" and hasAncestor(el, lambda x:x.tag=="a"):
            warn("The following (probably auto-generated) link is illegally nested in another link:\n{0}", outerHTML(el), el=el)

        # If the <h1> contains only capital letters, add a class=allcaps for styling hook
        if el.tag == "h1":
            for letter in textContent(el):
                if letter.isalpha() and letter.islower():
                    break
            else:
                addClass(el, "allcaps")

        # If a markdown-generated <dt> contains only a single paragraph,
        # remove that paragraph so it just contains naked text.
        if el.tag == "dt" and el.get("data-md") is not None:
            child = hasOnlyChild(el)
            if child is not None and child.tag == "p" and emptyText(el.text) and emptyText(child.tail):
                flattenEls.append(el)

        # Remove a bunch of attributes
        if el.get("data-attribute-info") is not None or el.get("data-dict-member-info") is not None:
            removeAttr(el, 'data-attribute-info')
            removeAttr(el, 'data-dict-member-info')
            removeAttr(el, 'for')
        if el.tag in ["a", "span"]:
            removeAttr(el, 'data-link-for')
            removeAttr(el, 'data-link-for-hint')
            removeAttr(el, 'data-link-status')
            removeAttr(el, 'data-link-spec')
            removeAttr(el, 'data-section')
            removeAttr(el, 'data-biblio-type')
            removeAttr(el, 'data-biblio-status')
            removeAttr(el, 'data-okay-to-fail')
            removeAttr(el, 'data-lt')
        if el.tag != "a":
            removeAttr(el, 'data-link-for')
            removeAttr(el, 'data-link-type')
        if el.tag not in config.dfnElements:
            removeAttr(el, 'data-dfn-for')
            removeAttr(el, 'data-dfn-type')
            removeAttr(el, 'data-export')
            removeAttr(el, 'data-noexport')
        if el.tag == "var":
            removeAttr(el, 'data-var-ignore')
        removeAttr(el, 'data-alternate-id')
        removeAttr(el, 'highlight')
        removeAttr(el, 'nohighlight')
        removeAttr(el, 'data-opaque')
        removeAttr(el, 'data-no-self-link')
        removeAttr(el, "line-number")
    for el in strayHeadEls:
        head.append(el)
    for el in styleScoped:
        parent = parentElement(el)
        prependChild(parent, el)
    for el in nestedLists:
        children = childNodes(el, clear=True)
        parent = parentElement(el)
        clearContents(parent)
        appendChild(parent, *children)
    for el in flattenEls:
        moveContents(fromEl=el[0], toEl=el)


def finalHackyCleanup(text):
    # For hacky last-minute string-based cleanups of the rendered html.

    return text


def hackyLineNumbers(lines):
    # Hackily adds line-number information to each thing that looks like an open tag.
    # This is just regex text-munging, so potentially dangerous!
    for i,line in enumerate(lines):
        lines[i] = re.sub(r"(^|[^<])(<[\w-]+)([ >])", r"\1\2 line-number={0}\3".format(i + 1), line)
    return lines


def correctH1(doc):
    # If you provided an <h1> manually, use that element rather than whatever the boilerplate contains.
    h1s = [h1 for h1 in findAll("h1", doc) if isNormative(h1)]
    if len(h1s) == 2:
        replaceNode(h1s[0], h1s[1])


def processInclusions(doc):
    import hashlib
    while True:
        els = findAll("pre.include", doc)
        if not els:
            break
        for el in els:
            macros = {}
            for i in itertools.count(0):
                m = el.get("macro-" + str(i))
                if m is None:
                    break
                k,_,v = m.partition(" ")
                macros[k.lower()] = v
            if el.get("path"):
                path = el.get("path")
                try:
                    with io.open(path, 'r', encoding="utf-8") as f:
                        lines = f.readlines()
                except Exception, err:
                    die("Couldn't find include file '{0}'. Error was:\n{1}", path, err, el=el)
                    removeNode(el)
                    continue
                # hash the content + path together for identity
                # can't use just path, because they're relative; including "foo/bar.txt" might use "foo/bar.txt" further nested
                # can't use just content, because then you can't do the same thing twice.
                # combined does a good job unless you purposely pervert it
                hash = hashlib.md5(path + ''.join(lines).encode("ascii", "xmlcharrefreplace")).hexdigest()
                if el.get('hash'):
                    # This came from another included file, check if it's a loop-include
                    if hash in el.get('hash'):
                        # WHOOPS
                        die("Include loop detected - “{0}” is included in itself.", path)
                        removeNode(el)
                        continue
                    hash += " " + el.get('hash')
                depth = int(el.get('depth')) if el.get('depth') is not None else 0
                if depth > 100:
                    # Just in case you slip past the nesting restriction
                    die("Nesting depth > 100, literally wtf are you doing.")
                    removeNode(el)
                    continue
                lines = datablocks.transformDataBlocks(doc, lines)
                lines = markdown.parse(lines, doc.md.indent, opaqueElements=doc.md.opaqueElements)
                text = ''.join(lines)
                text = doc.fixText(text, moreMacros=macros)
                subtree = parseHTML(text)
                for childInclude in findAll("pre.include", E.div({}, *subtree)):
                    childInclude.set("hash", hash)
                    childInclude.set("depth", str(depth + 1))
                replaceNode(el, *subtree)


def formatElementdefTables(doc):
    for table in findAll("table.elementdef", doc):
        elements = findAll("tr:first-child dfn", table)
        elementsFor = ' '.join(textContent(x) for x in elements)
        for el in findAll("a[data-element-attr-group]", table):
            groupName = textContent(el).strip()
            groupAttrs = sorted(doc.refs.queryRefs(linkType="element-attr", linkFor=groupName)[0], key=lambda x:x.text)
            if len(groupAttrs) == 0:
                die("The element-attr group '{0}' doesn't have any attributes defined for it.", groupName, el=el)
                continue
            el.tag = "details"
            clearContents(el)
            del el.attrib["data-element-attr-group"]
            del el.attrib["dfn"]
            ul = appendChild(el,
                             E.summary(
                                 E.a({"data-link-type":"dfn"}, groupName)),
                             E.ul())
            for ref in groupAttrs:
                appendChild(ul,
                            E.li(
                                E.dfn({"id":"element-attrdef-" + config.simplifyText(textContent(elements[0])) + "-" + ref.text, "for":elementsFor, "data-dfn-type":"element-attr"},
                                      E.a({"data-link-type":"element-attr", "for":groupName},
                                          ref.text.strip()))))


def formatArgumentdefTables(doc):
    for table in findAll("table.argumentdef", doc):
        forMethod = doc.widl.normalizedMethodNames(table.get("data-dfn-for"))
        method = doc.widl.find(table.get("data-dfn-for"))
        if not method:
            die("Can't find method '{0}'.", forMethod, el=table)
            continue
        for tr in findAll("tbody > tr", table):
            tds = findAll("td", tr)
            argName = textContent(tds[0]).strip()
            arg = method.findArgument(argName)
            if arg:
                appendChild(tds[1], unicode(arg.type))
                if unicode(arg.type).strip().endswith("?"):
                    appendChild(tds[2],
                                E.span({"class":"yes"}, "✔"))
                else:
                    appendChild(tds[2],
                                E.span({"class":"no"}, "✘"))
                if arg.optional:
                    appendChild(tds[3],
                                E.span({"class":"yes"}, "✔"))
                else:
                    appendChild(tds[3],
                                E.span({"class":"no"}, "✘"))
            else:
                die("Can't find the '{0}' argument of method '{1}' in the argumentdef block.", argName, method.fullName, el=table)
                continue


def inlineRemoteIssues(doc):
    # Finds properly-marked-up "remote issues",
    # and inlines their contents into the issue.

    # Right now, only github inline issues are supported.
    # More can be supported when someone cares.

    # Collect all the inline issues in the document
    inlineIssues = []
    GitHubIssue = collections.namedtuple('GitHubIssue', ['user', 'repo', 'num', 'el'])
    for el in findAll("[data-inline-github]", doc):
        inlineIssues.append(GitHubIssue(*el.get('data-inline-github').split(), el=el))
        removeAttr(el, "data-inline-github")
    if not inlineIssues:
        return

    logging.captureWarnings(True)

    responses = json.load(config.retrieveDataFile("github-issues.json", quiet=True))
    for i,issue in enumerate(inlineIssues):
        issueUserRepo = "{0}/{1}".format(*issue)
        key = "{0}/{1}".format(issueUserRepo, issue.num)
        href = "https://github.com/{0}/issues/{1}".format(issueUserRepo, issue.num)
        url = "https://api.github.com/repos/{0}/issues/{1}".format(issueUserRepo, issue.num)
        say("Fetching issue {:-3d}/{:d}: {:s}".format(i+1, len(inlineIssues), key))

        # Fetch the issues
        headers = {"Accept": "application/vnd.github.v3.html+json"}
        if doc.token is not None:
            headers["Authorization"] = "token " + doc.token
        if key in responses:
            # Have a cached response, see if it changed
            headers["If-None-Match"] = responses[key]["ETag"]

        res = requests.get(url, headers=headers)
        if res.status_code == 304:
            # Unchanged, I can use the cache
            data = responses[key]
        elif res.status_code == 200:
            # Fresh data, prep it for storage
            data = res.json()
            data["ETag"] = res.headers["ETag"]
        elif res.status_code == 401:
            error = res.json()
            if error["message"] == "Bad credentials":
                die("'{0}' is not a valid GitHub OAuth token. See https://github.com/settings/tokens", doc.token)
            else:
                die("401 error when fetching GitHub Issues:\n{0}", config.printjson(error))
            continue
        elif res.status_code == 403:
            error = res.json()
            if error["message"].startswith("API rate limit exceeded"):
                die("GitHub Issues API rate limit exceeded. Get an OAuth token from https://github.com/settings/tokens to increase your limit, or just wait an hour for your limit to refresh; Bikeshed has cached all the issues so far and will resume from where it left off.")
            else:
                die("403 error when fetching GitHub Issues:\n{0}", config.printjson(error))
            continue
        elif res.status_code >= 400:
            die("{0} error when fetching GitHub Issues:\n{1}", res.status_code, config.printjson(res.json()))
            continue
        responses[key] = data
        # Put the issue data into the DOM
        el = issue.el
        data = responses[key]
        clearContents(el)
        if doc.md.inlineGithubIssues == 'title':
            appendChild(el,
                    E.a({"href":href, "class":"marker", "style":"text-transform:none"}, key),
                    E.a({"href":href}, data['title']))
            addClass(el, "no-marker")
        else:
            appendChild(el,
                    E.a({"href":href, "class":"marker"},
                    "Issue #{0} on GitHub: “{1}”".format(data['number'], data['title'])),
                    *parseHTML(data['body_html']))
            addClass(el, "no-marker")
        if el.tag == "p":
            el.tag = "div"
    # Save the cache for later
    try:
        with io.open(config.scriptPath + "/spec-data/github-issues.json", 'w', encoding="utf-8") as f:
            f.write(unicode(json.dumps(responses, ensure_ascii=False, indent=2, sort_keys=True)))
    except Exception, e:
        warn("Couldn't save GitHub Issues cache to disk.\n{0}", e)
    return


def addNoteHeaders(doc):
    # Finds <foo heading="bar"> and turns it into a marker-heading
    for el in findAll("[heading]", doc):
        addClass(el, "no-marker")
        if hasClass(el, "note"):
            preText = "NOTE: "
        elif hasClass(el, "issue"):
            preText = "ISSUE: "
        elif hasClass(el, "example"):
            preText = "EXAMPLE: "
        else:
            preText = ""
        prependChild(el,
                     E.div({"class":"marker"}, preText, *parseHTML(el.get('heading'))))
        removeAttr(el, "heading")


def locateFillContainers(doc):
    fillContainers = defaultdict(list)
    for el in findAll("[data-fill-with]", doc):
        fillContainers[el.get("data-fill-with")].append(el)
    return fillContainers
