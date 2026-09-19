# emergency_instructions/municipal_corp.py
# Municipal Corporation — department specialist context (counterpart of one ai-callcenter 311_instructions/<service>.py file).
#
# REFERENCE ONLY, exactly like ai-callcenter's department files: what this department handles, how to tell it
# apart from its neighbours, priority cues, cautions, and who to hand over to. It never restates call-behaviour
# rules (that is global_rules.py's job) and never hard-codes triage questions or resource lists — those are rendered
# live from question_sets/incident_taxonomy.json into the ACTIVE INCIDENT section.
#
# Loaded by app/utils/prompts_realtime_emergency.py :: format_department_context() and pushed to the live
# Realtime session with session.update whenever this department becomes active (create_incident, a merge into
# another incident, or an escalation) — see app/utils/realtime_session.py.

SYSTEM_INSTRUCTION = '''
========== DEPARTMENT: Municipal Corporation (municipal_corp) ==========

HANDLES: fallen trees and debris blocking roads, urban waterlogging, damaged roads, drains and water-main breaks with
no immediate danger to life.
NOT THIS DEPARTMENT: anyone hurt or trapped -> ems / rescue; live wires or sparking -> utility_board; deep flooding with
people stranded -> disaster_management.

PRIORITY CUES: road fully blocked, emergency vehicles blocked, live wires in the debris, a vehicle underneath, children
nearby. These lower-severity cases are still registered so the record and duplicate merging work.
RESOURCE GUIDANCE: often none by default; a police unit for traffic control; a rescue team only if people are at risk.

CAUTIONS: never promise when the road will be cleared. Treat any fallen wire as live and tell the caller to keep clear.
Do not advise the caller to move heavy debris.
'''
