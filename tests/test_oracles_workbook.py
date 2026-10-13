"""Baghara's workbooks through the oracle harness, and the reader that reads them.

The reader names every input and figure mechanically, so the ways it could
pass a workbook dishonestly are each pinned: a number typed inside a formula
is an input like a typed cell; a formula cell that saved no number is set
aside by the oracle with what it saved; IRR, XIRR and RATE cells keep Excel's
documented 1e-7 and every other cell the drift of double arithmetic; a figure
the translation neither reads nor sets aside fails as "not reported".

Then every workbook gets its recorded verdict: a translated workbook
matches, or differs on exactly the figures its own formula slips (or linked
files that disagree) get wrong, each worked out in its translation's header;
the planted error is caught; a workbook with no translation yet is blocked,
saying so.
"""
from fractions import Fraction

import pytest
import yaml

from one_engine import oracles as O
from one_engine.oracles.workbook import (FOLDER, ITERATIVE_TOL, WorkbookFamily, _literals, read_workbook,
                                         sheet_key)

FAMILY = WorkbookFamily()
ORACLES = FAMILY.oracles()
#: the workbooks vendored from Baghara (02-Financial-Models and 03_evidence/promoter-originals, and v15.24
#: from the golds corpus), the ModelOff answer and data workbooks, Bodmer's solar case (a macro workbook,
#: .xlsm) and Macabacus's two LBO templates
VENDORED = {"nour_3moissons_haraz", "van_10ans_previsions_3moissons", "cap_3moissons_estimation_2025",
            "business_plan_3moissons_2021", "cattle_integration_financial_model", "haraz_financial_model_2013",
            "baghara_v15_24", "modeloff_2012_asset_schedule", "modeloff_2012_investment_property",
            "modeloff_2013_hard_times", "modeloff_2014_time_is_money", "modeloff_2015_more_money_please",
            "modeloff_2015_tax_solution", "bodmer_solar_case_study", "macabacus_lbo_short", "macabacus_lbo_long"}
#: the figures each translated workbook gets wrong by its own formula slips, or by linked files that
#: disagree with each other (each translation's header works every one out); the workbook wins
SLIPS = {
    "nour_3moissons_haraz": {"VAN_Nour_Baghara.C54:F54", "VAN_Nour_Baghara.C55:F55", "VAN_Nour_Baghara.C46:W46",
                             "VAN_Nour_Baghara.C47:W47"},
    "van_10ans_previsions_3moissons": {
        # PL row 33 takes the later cohorts' overheads (rows 90, 117) for their one-off charges (89, 116)
        "PL.C33:J33", "PL.C40:J40", "PL.C43:J43", "PL.C47:J47", "_50_CAP.C37:J37",
        "VAN_des_CAP.D43:O43", "VAN_des_CAP.D46:O46", "VAN_des_CAP.D47:O47",
        "VAN_des_CAP.C48", "VAN_des_CAP.C49", "VAN_des_CAP.C51", "VAN_des_CAP.C52",
        # Invest I38:J38 repeat year 5's depreciation where the P&L charges the imported year-6 amount
        "Invest.C24:J24", "Invest.C25:J25", "Invest.I38:J38", "Invest.E49:J49",
        # the per-CAP loan balance is stitched from two linked files and is not draws less repayments
        "Emprunts.D30:K30", "Emprunts.F206:K206", "Emprunts.G223:K223", "_50_CAP.D109:J109"},
    "baghara_v15_24": {
        # 08_OpEx row 17, the DSRA LC fee, is charged but left out of the opex totals (rows 7 and 71);
        # with the fee set to nil every one of these agrees
        "_00_Cover.D24", "_00_Cover.D25", "_00_Cover.D26", "_08_OpEx.D10:L10", "_08_OpEx.D71:L71",
        "_08_OpEx.D7:L7", "_12_WC.D13:L13", "_12_WC.D15:L15", "_12_WC.D9:L9", "_13_Tax.D12:L12",
        "_13_Tax.D16:L16", "_13_Tax.D17:L17", "_13_Tax.D18:L18", "_13_Tax.D19:L19", "_13_Tax.D9:L9",
        "_14_IS.D13:L13", "_14_IS.D18:L18", "_14_IS.D22:L22", "_14_IS.D27:L27", "_14_IS.D8:L8", "_14_IS.D9:L9",
        "_15_BS.D13:L13", "_15_BS.D16:L16", "_15_BS.D20:L20", "_15_BS.D24:L24", "_15_BS.D26:L26",
        "_15_BS.D27:L27", "_15_BS.D8:L8", "_16_CFS.D11:L11", "_16_CFS.D13:L13", "_16_CFS.D26:L26",
        "_16_CFS.D28:L28", "_16_CFS.D29:L29", "_16_CFS.D8:L8", "_17_Outputs.D11:L11", "_17_Outputs.D14:L14",
        "_17_Outputs.D16:L16", "_17_Outputs.D18:L18", "_17_Outputs.D25", "_17_Outputs.D29:L29",
        "_17_Outputs.D30", "_17_Outputs.D34", "_17_Outputs.D35", "_17_Outputs.D36", "_17_Outputs.D37",
        "_17_Outputs.D38", "_17_Outputs.D8:L8", "_17_Outputs.D9:L9", "driver _17_Outputs.D22",
        "driver _17_Outputs.F22:L22",
        # 09_Debt D13's total is one year's interest, not the sum of the years
        "_09_Debt.D13:L13"},
    "business_plan_3moissons_2021": {
        # fourteen slips, each named with its cell and both numbers in the translation's header (S1-S14):
        # Dep: technical buildings taxed at 15% not 5% (S1), miscellaneous assets divided by 100,000 (S2),
        # straight line run past cost in year 10 (S3); these move the whole P&L below variable costs
        "Dep.B15:L15", "Dep.B17:L17", "Dep.B22:L22", "Dep.B23:L23", "Dep.B32:L32", "Dep.B38:L38",
        "Dep.B39:L39", "Dep.C20:L20", "Dep.C31:L31", "Dep.C34:L34", "Dep.C42:L42",
        "P_L.D36:M36", "P_L.D38:M38", "P_L.D40:M40", "P_L.D42:M42", "P_L.D45:M45", "P_L.D46:M46",
        "P_L.D47:M47", "P_L.D48:M48", "P_L.D50:M50", "P_L.D54:M54", "P_L.D55:M55", "P_L.D56:M56",
        "P_L.D57:M57", "P_L.D58:M58", "P_L.D60:L60", "P_L.D66:L66", "P_L.D67:L67", "P_L.D68:L68",
        "P_L.C75:L75", "P_L.D83:L83", "P_L.C84:L84", "P_L.D85:L85", "P_L.D92:M92", "P_L.D94:M94",
        "P_L.D95:M95", "P_L.D99:M99", "P_L.D100:M100", "P_L.D102:M102", "P_L.D103:M103", "P_L.D104:M104",
        "P_L.D109:L109", "P_L.D110:L110", "P_L.D111:L111", "P_L.D112:L112", "P_L.D113:L113",
        "P_L.D114:L114", "P_L.D115", "P_L.D118:L118", "P_L.D124:L124", "P_L.D125", "P_L.D129:L129",
        "P_L.D136:J136", "P_L.D137", "P_L.D142:L142", "P_L.D143:L143", "P_L.D145:L145", "P_L.C154:M154",
        "P_L.C155", "P_L.D159:L159", "P_L.D160:L160", "P_L.D162:L162", "P_L.C167:L167", "P_L.D176:L176",
        "P_L.D177:L177", "P_L.D178:L178", "P_L.D179:L179", "P_L.D181:L181", "P_L.D183:L183",
        "P_L.F199:L199", "P_L.D202:L202", "P_L.D204:L204", "P_L.F205:L205", "P_L.F214:L214",
        "P_L.D217:L217", "P_L.F218:L218", "P_L.D229:L229", "P_L.D230:L230", "driver P_L.D232:L232",
        "P_L.C239", "P_L.C240", "P_L.C241",
        # Growing cost: three crop totals add the hectares (S11-S13), aubergine margin subtracts the yield (S14)
        "Growing_cost_assum.B15:L15", "Growing_cost_assum.C20:L20", "Growing_cost_assum.B107:L107",
        "Growing_cost_assum.C112:L112", "Growing_cost_assum.C149:L149", "Growing_cost_assum.B198:L198",
        "Growing_cost_assum.C203:L203"},
    "cap_3moissons_estimation_2025": {
        # ten slips, each named with its cell and both numbers in the translation's header: totals that
        # leave out rows (Amenag N10, Mat bureau R9, the Salaires totals), Exploitation row 72 adding the
        # year label, CA Elevage leaving out the poultry charges, CRP charging last year's annuity as
        # interest, Coût F15 adding two working-capital levels, BFR row 18 subtracting a change, and the
        # euro budget's crop totals; against the slip-corrected workbook every check agrees
        'Amenag_Infrastr_et_Equipemt.G58', 'Amenag_Infrastr_et_Equipemt.L55',
        'Amenag_Infrastr_et_Equipemt.L59', 'Amenag_Infrastr_et_Equipemt.N10',
        'Amenag_Infrastr_et_Equipemt.N11', 'Amort_technique.D11', 'Amort_technique.D13',
        'Amort_technique.D8', 'Amort_technique.F13:J13', 'Amort_technique.G11:J11', 'Amort_technique.G8:J8',
        'BFR.E11:I11', 'BFR.E12:I12', 'BFR.E15:I15', 'BFR.E16:I16', 'BFR.E17:I17', 'BFR.F18:I18',
        'CA_Elevage.C204:G204', 'CRP.C11:G11', 'CRP.C12:G12', 'CRP.C17:G17', 'CRP.C19:G19', 'CRP.C20:G20',
        'CRP.C23:G23', 'CRP.C25:G25', 'CRP.C27:G27', 'CRP.C32:G32', 'CRP.C36:G36', 'CRP.C38:G38',
        'CRP.C40:G40', 'CRP.C6:G6', 'CRP.D29:G29', 'CRP.D30:G30', 'CRP.I11:K11', 'CRP.I12:K12',
        'CRP.I17:K17', 'CRP.I20:K20', 'CRP.I23:K23', 'CRP.I25:K25', 'CRP.I27:K27', 'CRP.I29:K29',
        'CRP.I30:K30', 'CRP.I32:K32', 'CRP.I36:K36', 'CRP.I38:K38', 'CRP.I40:K40', 'CRP.I6:K6', 'Cout.F13',
        'Cout.F14', 'Cout.F15', 'Cout.F17', 'Couts_de_product_Agricole.C109:M109',
        'Couts_de_product_Agricole.C17:M17', 'Couts_de_product_Agricole.C199:M199',
        'Couts_de_product_Agricole.C209:M209', 'Couts_de_product_Agricole.C63:M63',
        'Couts_de_product_Agricole.C64:M64', 'Couts_de_product_Agricole.C65:M65',
        'Couts_de_product_Agricole.C66:M66', 'Couts_de_product_Agricole.C67:M67',
        'Couts_de_product_Agricole.C68:M68', 'Couts_de_product_Agricole.C69:M69',
        'Couts_de_product_Agricole.C70:M70', 'Couts_de_product_Agricole.C71:M71',
        'Couts_de_product_Agricole.D114:M114', 'Couts_de_product_Agricole.D150:M150',
        'Couts_de_product_Agricole.D204:M204', 'Couts_de_product_Agricole.D211:M211',
        'Couts_de_product_Agricole.D213:M213', 'Couts_de_product_Agricole.D22:M22',
        'Couts_de_product_Agricole.D62:M62', 'Couts_de_product_Agricole.D74:M74',
        'Couts_de_product_Agricole.D76:M76', 'Diverses_charges_immobilisees.F43',
        'Diverses_charges_immobilisees.F45', 'Diverses_charges_immobilisees.F48',
        'Diverses_charges_immobilisees.F58', 'Diverses_charges_immobilisees.F60', 'Exploitation.F72:J72',
        'Exploitation.F80:J80', 'Exploitation.F82:J82', 'Mat_bureau_Mat_roulants.I76',
        'Mat_bureau_Mat_roulants.R10', 'Mat_bureau_Mat_roulants.R64', 'Mat_bureau_Mat_roulants.R68',
        'Mat_bureau_Mat_roulants.R9', 'Salaires_Avantages.F100:J100', 'Salaires_Avantages.F101:J101',
        'Salaires_Avantages.F102:J102', 'Salaires_Avantages.F134:J134', 'Salaires_Avantages.F142:J142',
        'Salaires_Avantages.F143:J143', 'Salaires_Avantages.F144:J144', 'Salaires_Avantages.F145:J145',
        'Salaires_Avantages.F146:J146', 'Salaires_Avantages.F148:J148', 'Salaires_Avantages.F149:J149',
        'Salaires_Avantages.F150:J150', 'Salaires_Avantages.F49:J49', 'Salaires_Avantages.F50:J50',
        'Salaires_Avantages.F51:J51', 'Salaires_Avantages.F52:J52', 'Salaires_Avantages.F53:J53',
        'Salaires_Avantages.G103:J103', 'Salaires_Avantages.G136:J136', 'Salaires_Avantages.L148',
        'Schema_de_financement.H14:I14', 'Schema_de_financement.H9', 'driver Schema_de_financement.I10',
        'driver Schema_de_financement.I11', 'driver Schema_de_financement.I12'},
    "bodmer_solar_case_study": {
        # two slips (120 checks), named with their cells in the translation's header: Operating Model K96
        # adds the capacity in kW (row 87) to the quarter's opex, which moves the sculpted debt and
        # everything after it; and the I-column generation totals stop at column BR, not DQ
        'Financing_Model.G127', 'Financing_Model.G191', 'Financing_Model.G62', 'Financing_Model.G66:H66',
        'Financing_Model.G67:H67', 'Financing_Model.G68', 'Financing_Model.G69', 'Financing_Model.G81',
        'Financing_Model.G93', 'Financing_Model.G94', 'Financing_Model.H34', 'Financing_Model.I102',
        'Financing_Model.I103', 'Financing_Model.I104', 'Financing_Model.I114', 'Financing_Model.I115',
        'Financing_Model.I120', 'Financing_Model.I121', 'Financing_Model.I122', 'Financing_Model.I124',
        'Financing_Model.I131', 'Financing_Model.I22', 'Financing_Model.J55', 'Financing_Model.J56',
        'Financing_Model.J57', 'Financing_Model.J58', 'Financing_Model.K102:DQ102', 'Financing_Model.K103:DQ103',
        'Financing_Model.K104:DQ104', 'Financing_Model.K110:DQ110', 'Financing_Model.K113:DQ113',
        'Financing_Model.K114:DQ114', 'Financing_Model.K115:DQ115', 'Financing_Model.K117:DQ117',
        'Financing_Model.K120:DQ120', 'Financing_Model.K121:DQ121', 'Financing_Model.K122:DQ122',
        'Financing_Model.K124:DQ124', 'Financing_Model.K127:DQ127', 'Financing_Model.K128:DQ128',
        'Financing_Model.K129:DQ129', 'Financing_Model.K131:DQ131', 'Financing_Model.K13:DQ13',
        'Financing_Model.K150:DQ150', 'Financing_Model.K152:DQ152', 'Financing_Model.K153:DQ153',
        'Financing_Model.K154:DQ154', 'Financing_Model.K155:DQ155', 'Financing_Model.K156:DQ156',
        'Financing_Model.K157:DQ157', 'Financing_Model.K158:DQ158', 'Financing_Model.K15:DQ15',
        'Financing_Model.K160:DQ160', 'Financing_Model.K161:DQ161', 'Financing_Model.K166:DQ166',
        'Financing_Model.K167:DQ167', 'Financing_Model.K169:DQ169', 'Financing_Model.K16:DQ16',
        'Financing_Model.K170:DQ170', 'Financing_Model.K171:DQ171', 'Financing_Model.K172:DQ172',
        'Financing_Model.K175:DQ175', 'Financing_Model.K177:DQ177', 'Financing_Model.K180:DQ180',
        'Financing_Model.K183:DQ183', 'Financing_Model.K184:DQ184', 'Financing_Model.K198:DQ198',
        'Financing_Model.K206:DQ206', 'Financing_Model.K207:DQ207', 'Financing_Model.K22:DQ22',
        'Financing_Model.K33:DQ33', 'Financing_Model.K34:DQ34', 'Financing_Model.K35:DQ35',
        'Financing_Model.K36:DQ36', 'Financing_Model.K37:DQ37', 'Financing_Model.K75:DQ75',
        'Financing_Model.K76:DQ76', 'Financing_Model.K77:DQ77', 'Financing_Model.K78:DQ78',
        'Financing_Model.K81:DQ81', 'Financing_Model.K85:DQ85', 'Financing_Model.K88:DQ88',
        'Financing_Model.K92:DQ92', 'Financing_Model.K93:DQ93', 'Financing_Model.K94:DQ94',
        'Financing_Model.K96:DQ96', 'Financing_Model.K97:DQ97', 'Financing_Model.L201:DQ201',
        'Financing_Model.T185:CU185', 'Operating_Model.G31:I31', 'Operating_Model.G32:I32',
        'Operating_Model.G33:I33', 'Operating_Model.G34:I34', 'Operating_Model.G35:I35', 'Operating_Model.G36:I36',
        'Operating_Model.G37:I37', 'Operating_Model.G38:I38', 'Operating_Model.G39:I39', 'Operating_Model.G40:I40',
        'Operating_Model.G41:I41', 'Operating_Model.G42:I42', 'Operating_Model.I96', 'Operating_Model.K104:DQ104',
        'Operating_Model.K142:DQ142', 'Operating_Model.K144:DQ144', 'Operating_Model.K156:DQ156',
        'Operating_Model.K157:DQ157', 'Operating_Model.K158:DQ158', 'Operating_Model.K159:DQ159',
        'Operating_Model.K160:DQ160', 'Operating_Model.K162:DQ162', 'Operating_Model.K163:DQ163',
        'Operating_Model.K164:DQ164', 'Operating_Model.K96:DQ96', 'Summary.F30', 'Summary.F31', 'Summary.F7',
        'driver Financing_Model.I10', 'driver Operating_Model.I10', 'driver Operating_Model.I43',
        # not slips (35 checks): figures that wait on engine pieces the header lists: XIRR on dated
        # flows, NORM.INV, a minimum over periods, a ratio of two readings, running totals of a record
        # field, flat per-tier waterfall payments, and a standby loan that lends a service shortfall
        'Summary.F2', 'Summary.F3', 'Summary.F4', 'Summary.F5', 'Summary.F8', 'Summary.F9', 'Summary.F22',
        'Assumptions.F29', 'Assumptions.F30', 'Scenario.R6', 'Operating_Model.G146', 'Operating_Model.G166',
        'Operating_Model.G178', 'Financing_Model.K82:DQ82', 'Financing_Model.K83:DQ83', 'Financing_Model.K87:DQ87',
        'Financing_Model.K89:DQ89', 'Financing_Model.K116:DQ116', 'Financing_Model.K134:DQ134',
        'Financing_Model.K135:DQ135', 'Financing_Model.K136:DQ136', 'Financing_Model.K137:DQ137',
        'Financing_Model.K139:DQ139', 'Financing_Model.K173:DQ173', 'Financing_Model.K174:DQ174',
        'Financing_Model.K176:DQ176', 'Financing_Model.G181', 'Financing_Model.G185', 'Financing_Model.G193',
        'Financing_Model.T199:CU199', 'Financing_Model.G202', 'Financing_Model.K208:DQ208',
        'Financing_Model.K209:DQ209', 'Financing_Model.K210:DQ210', 'Financing_Model.T213:CU213'},
    "cattle_integration_financial_model": {
        # five slips, each named with its cells and both numbers in the translation's header: (a) the
        # quarterly loan table stops at quarter 40 of 60, so loan 1 still owes 17.6M at year 20; (b) year 20
        # reads the blank Data Input column W; (c) OPEX H89 ramps beef maintenance with the dairy ramp;
        # (d) Beef Herd O25:O28 add the year-1 totals; (e) the sensitivity tables hold the inner data tables
        # at base, so no sensitivity re-sizes the working-capital loans
        'CAPEX_Financing.C101:W101', 'CAPEX_Financing.C103:W103', 'CAPEX_Financing.C116:W116',
        'CAPEX_Financing.C117:W117', 'CAPEX_Financing.C130:W130', 'CAPEX_Financing.C132:W132',
        'CAPEX_Financing.C145:W145', 'CAPEX_Financing.C146:W146', 'CAPEX_Financing.C23:W23',
        'CAPEX_Financing.C24:W24', 'CAPEX_Financing.C26:W26', 'CAPEX_Financing.C27:W27', 'CAPEX_Financing.C43:W43',
        'CAPEX_Financing.C45:W45', 'CAPEX_Financing.C58:W58', 'CAPEX_Financing.C59:W59', 'CAPEX_Financing.C72:W72',
        'CAPEX_Financing.C74:W74', 'CAPEX_Financing.C87:W87', 'CAPEX_Financing.C88:W88',
        'CAPEX_Financing.D102:W102', 'CAPEX_Financing.D131:W131', 'CAPEX_Financing.D44:W44',
        'CAPEX_Financing.D73:W73', 'Dashboard.C108', 'Dashboard.C109', 'Dashboard.C11', 'Dashboard.C116',
        'Dashboard.C117', 'Dashboard.C124', 'Dashboard.C125', 'Dashboard.C132', 'Dashboard.C133',
        'Dashboard.C22:Y22', 'Dashboard.C29:Z29', 'Dashboard.C6:D6', 'Dashboard.C7:D7', 'Dashboard.D112:H112',
        'Dashboard.D120:H120', 'Dashboard.D128:H128', 'Dashboard.D136:H136', 'Dashboard.E36:Y36',
        'Dashboard.E38:Y38', 'Dashboard.E39:Y39', 'Dashboard.E52:Y52', 'Dashboard.E53:Y53', 'Dashboard.E54:Y54',
        'Dashboard.E55:Y55', 'Dashboard.E56:Y56', 'Data_Input.D7:V7', 'Fin_Statements.AA10:AU10',
        'Fin_Statements.AA11:AU11', 'Fin_Statements.AA12:AU12', 'Fin_Statements.AA13:AU13',
        'Fin_Statements.AA14:AU14', 'Fin_Statements.AA15:AU15', 'Fin_Statements.AA16:AU16',
        'Fin_Statements.AA23:AU23', 'Fin_Statements.AA25:AU25', 'Fin_Statements.AA26:AU26',
        'Fin_Statements.AA27:AU27', 'Fin_Statements.AA28:AU28', 'Fin_Statements.AA31:AU31',
        'Fin_Statements.AA32:AU32', 'Fin_Statements.AA35:AU35', 'Fin_Statements.AA40:AU40',
        'Fin_Statements.AA41:AU41', 'Fin_Statements.AA42:AU42', 'Fin_Statements.AA43:AU43',
        'Fin_Statements.AA49:AU49', 'Fin_Statements.AA51:AU51', 'Fin_Statements.AA52:AU52',
        'Fin_Statements.AA54:AU54', 'Fin_Statements.AA55:AU55', 'Fin_Statements.AA56:AU56',
        'Fin_Statements.AA57:AU57', 'Fin_Statements.AA59:AU59', 'Fin_Statements.AA5:AU5',
        'Fin_Statements.AA63:AU63', 'Fin_Statements.AA64:AU64', 'Fin_Statements.AA65:AU65',
        'Fin_Statements.AA66:AU66', 'Fin_Statements.AA67:AU67', 'Fin_Statements.AA68:AU68',
        'Fin_Statements.AA6:AU6', 'Fin_Statements.AA74:AU74', 'Fin_Statements.AA75:AU75',
        'Fin_Statements.AA76:AU76', 'Fin_Statements.AA77:AU77', 'Fin_Statements.AA7:AU7',
        'Fin_Statements.AA82:AU82', 'Fin_Statements.AA84:AU84', 'Fin_Statements.AA85:AU85',
        'Fin_Statements.AA86:AU86', 'Fin_Statements.AA87:AU87', 'Fin_Statements.AA88:AU88',
        'Fin_Statements.AA8:AU8', 'Fin_Statements.AA90:AU90', 'Fin_Statements.AA91:AU91',
        'Fin_Statements.AA92:AU92', 'Fin_Statements.C105:D105', 'Fin_Statements.C106:D106',
        'Fin_Statements.C10:W10', 'Fin_Statements.C116:D116', 'Fin_Statements.C117:D117', 'Fin_Statements.C11:W11',
        'Fin_Statements.C120:D120', 'Fin_Statements.C121:D121', 'Fin_Statements.C12:W12', 'Fin_Statements.C13:W13',
        'Fin_Statements.C14:W14', 'Fin_Statements.C15:W15', 'Fin_Statements.C16:W16', 'Fin_Statements.C23:W23',
        'Fin_Statements.C28:W28', 'Fin_Statements.C31:W31', 'Fin_Statements.C32:W32', 'Fin_Statements.C35:W35',
        'Fin_Statements.C40:W40', 'Fin_Statements.C41:W41', 'Fin_Statements.C42:W42', 'Fin_Statements.C43:W43',
        'Fin_Statements.C49:W49', 'Fin_Statements.C51:W51', 'Fin_Statements.C52:W52', 'Fin_Statements.C54:X54',
        'Fin_Statements.C55:X55', 'Fin_Statements.C56:X56', 'Fin_Statements.C57:X57', 'Fin_Statements.C59:W59',
        'Fin_Statements.C5:W5', 'Fin_Statements.C63:W63', 'Fin_Statements.C64:W64', 'Fin_Statements.C65:X65',
        'Fin_Statements.C66:X66', 'Fin_Statements.C67:X67', 'Fin_Statements.C68:X68', 'Fin_Statements.C6:W6',
        'Fin_Statements.C74:W74', 'Fin_Statements.C77:W77', 'Fin_Statements.C7:W7', 'Fin_Statements.C82:W82',
        'Fin_Statements.C85:W85', 'Fin_Statements.C86:W86', 'Fin_Statements.C87:W87', 'Fin_Statements.C88:W88',
        'Fin_Statements.C8:W8', 'Fin_Statements.C90:W90', 'Fin_Statements.C91:W91', 'Fin_Statements.C92:W92',
        'Fin_Statements.D100:M100', 'Fin_Statements.D25:W25', 'Fin_Statements.D26:W26', 'Fin_Statements.D27:W27',
        'Fin_Statements.D75:W75', 'Fin_Statements.D76:W76', 'Fin_Statements.D84:W84', 'Fin_Statements.D98:W98',
        'Fin_Statements.D99:W99', 'OPEX.C120:J120', 'OPEX.C122:J122', 'OPEX.C124:J124', 'OPEX.H109:S109',
        'OPEX.H111:S111', 'OPEX.H89:S89', 'OPEX.H92:S92', 'OPEX.H93:S93', 'OPEX.H95:S95', 'WC_Financing.C100:W100',
        'WC_Financing.C102:W102', 'WC_Financing.C15:E15', 'WC_Financing.C217:W217', 'WC_Financing.C219:W219',
        'WC_Financing.C221:W221', 'WC_Financing.C234:W234', 'WC_Financing.C236:W236', 'WC_Financing.C238:W238',
        'WC_Financing.C251:W251', 'WC_Financing.C253:W253', 'WC_Financing.C255:W255', 'WC_Financing.C268:W268',
        'WC_Financing.C270:W270', 'WC_Financing.C272:W272', 'WC_Financing.C302:W302', 'WC_Financing.C304:W304',
        'WC_Financing.C306:W306', 'WC_Financing.C319:W319', 'WC_Financing.C321:W321', 'WC_Financing.C323:W323',
        'WC_Financing.C336:W336', 'WC_Financing.C338:W338', 'WC_Financing.C340:W340', 'WC_Financing.C353:W353',
        'WC_Financing.C355:W355', 'WC_Financing.C357:W357', 'WC_Financing.C47:W47', 'WC_Financing.C49:W49',
        'WC_Financing.C51:W51', 'WC_Financing.C64:W64', 'WC_Financing.C66:W66', 'WC_Financing.C68:W68',
        'WC_Financing.C81:W81', 'WC_Financing.C83:W83', 'WC_Financing.C85:W85', 'WC_Financing.C98:W98',
        'WC_Financing.D213:W213', 'WC_Financing.D214:W214', 'WC_Financing.D215:W215', 'WC_Financing.D218:W218',
        'WC_Financing.D21:M21', 'WC_Financing.D230:W230', 'WC_Financing.D231:W231', 'WC_Financing.D232:W232',
        'WC_Financing.D235:W235', 'WC_Financing.D247:W247', 'WC_Financing.D248:W248', 'WC_Financing.D249:W249',
        'WC_Financing.D24:M24', 'WC_Financing.D252:W252', 'WC_Financing.D264:W264', 'WC_Financing.D265:W265',
        'WC_Financing.D266:W266', 'WC_Financing.D269:W269', 'WC_Financing.D28:W28', 'WC_Financing.D298:W298',
        'WC_Financing.D299:W299', 'WC_Financing.D29:W29', 'WC_Financing.D300:W300', 'WC_Financing.D303:W303',
        'WC_Financing.D315:W315', 'WC_Financing.D316:W316', 'WC_Financing.D317:W317', 'WC_Financing.D31:W31',
        'WC_Financing.D320:W320', 'WC_Financing.D332:W332', 'WC_Financing.D333:W333', 'WC_Financing.D334:W334',
        'WC_Financing.D337:W337', 'WC_Financing.D349:W349', 'WC_Financing.D350:W350', 'WC_Financing.D351:W351',
        'WC_Financing.D354:W354', 'WC_Financing.D43:W43', 'WC_Financing.D44:W44', 'WC_Financing.D45:W45',
        'WC_Financing.D48:W48', 'WC_Financing.D60:W60', 'WC_Financing.D61:W61', 'WC_Financing.D62:W62',
        'WC_Financing.D65:W65', 'WC_Financing.D77:W77', 'WC_Financing.D78:W78', 'WC_Financing.D79:W79',
        'WC_Financing.D82:W82', 'WC_Financing.D94:W94', 'WC_Financing.D95:W95', 'WC_Financing.D96:W96',
        'WC_Financing.D99:W99', 'driver Beef_Herd_Movement.C25:AL25', 'driver Beef_Herd_Movement.C26:AL26',
        'driver Beef_Herd_Movement.C27:AL27', 'driver Beef_Herd_Movement.C28:AL28', 'driver Dashboard.C12',
        'driver Dashboard.C14', 'driver Dashboard.E40:Y40', 'driver Dashboard.E43:Y43', 'driver Dashboard.E44:Y44',
        'driver Dashboard.E45:Y45', 'driver Dashboard.E46:Y46', 'driver Dashboard.E47:Y47',
        'driver Dashboard.E48:Y48', 'driver Dashboard.E49:Y49', 'driver Dashboard.E50:Y50',
        'driver Fin_Statements.AB17:AT17', 'driver Fin_Statements.D17:V17'},
    "haraz_financial_model_2013": {
        # the cattle model's slips (a)-(e) with Haraz's numbers, and (f): the data tables were not
        # recomputed after the last input change (calculation 'automatic except tables'), so loan 1's size
        # A54 and its IDC C54 disagree by 36.63 and Fin.Statements row 101 and the sensitivities are stale
        'CAPEX.C266:H266', 'CAPEX.C267:H267', 'CAPEX_Financing.A54', 'CAPEX_Financing.A55', 'CAPEX_Financing.A83',
        'CAPEX_Financing.A84', 'CAPEX_Financing.C10:I10', 'CAPEX_Financing.C18:G18', 'CAPEX_Financing.C19:G19',
        'CAPEX_Financing.C20:G20', 'CAPEX_Financing.C21:W21', 'CAPEX_Financing.C23:W23', 'CAPEX_Financing.C24:W24',
        'CAPEX_Financing.C26:W26', 'CAPEX_Financing.C27:W27', 'CAPEX_Financing.C38:AP38',
        'CAPEX_Financing.C39:AP39', 'CAPEX_Financing.C40:AP40', 'CAPEX_Financing.C43:W43',
        'CAPEX_Financing.C45:W45', 'CAPEX_Financing.C49:N49', 'CAPEX_Financing.C50:N50', 'CAPEX_Financing.C51:O51',
        'CAPEX_Financing.C54', 'CAPEX_Financing.C55', 'CAPEX_Financing.C58:W58', 'CAPEX_Financing.C59:W59',
        'CAPEX_Financing.C67:AP67', 'CAPEX_Financing.C68:AP68', 'CAPEX_Financing.C69:AP69',
        'CAPEX_Financing.C72:W72', 'CAPEX_Financing.C74:W74', 'CAPEX_Financing.C78:N78', 'CAPEX_Financing.C79:N79',
        'CAPEX_Financing.C80:O80', 'CAPEX_Financing.C83', 'CAPEX_Financing.C84', 'CAPEX_Financing.C87:W87',
        'CAPEX_Financing.C88:W88', 'CAPEX_Financing.D44:W44', 'CAPEX_Financing.D73:W73', 'Dashboard.AC20:AI20',
        'Dashboard.AC21:AI21', 'Dashboard.AC22:AI22', 'Dashboard.AC24:AI24', 'Dashboard.AC25:AI25',
        'Dashboard.AC27:AI27', 'Dashboard.AC28:AI28', 'Dashboard.C108', 'Dashboard.C109', 'Dashboard.C11',
        'Dashboard.C116', 'Dashboard.C117', 'Dashboard.C124', 'Dashboard.C125', 'Dashboard.C13', 'Dashboard.C132',
        'Dashboard.C133', 'Dashboard.C20:Z20', 'Dashboard.C21:Z21', 'Dashboard.C22:Y22', 'Dashboard.C23:Z23',
        'Dashboard.C25:Z25', 'Dashboard.C26:Z26', 'Dashboard.C28:I28', 'Dashboard.C29:Z29', 'Dashboard.C30:Z30',
        'Dashboard.C6:D6', 'Dashboard.C7:D7', 'Dashboard.D112:H112', 'Dashboard.D120:H120', 'Dashboard.D128:H128',
        'Dashboard.D136:H136', 'Dashboard.E36:Y36', 'Dashboard.E38:Y38', 'Dashboard.E39:Y39', 'Dashboard.E52:Y52',
        'Dashboard.E53:Y53', 'Dashboard.E54:Y54', 'Dashboard.E55:Y55', 'Dashboard.E56:Y56', 'Data_Input.D7:V7',
        'Fin_Statements.AA10:AU10', 'Fin_Statements.AA11:AU11', 'Fin_Statements.AA12:AU12',
        'Fin_Statements.AA13:AU13', 'Fin_Statements.AA14:AU14', 'Fin_Statements.AA15:AU15',
        'Fin_Statements.AA16:AU16', 'Fin_Statements.AA23:AU23', 'Fin_Statements.AA25:AU25',
        'Fin_Statements.AA26:AU26', 'Fin_Statements.AA27:AU27', 'Fin_Statements.AA28:AU28',
        'Fin_Statements.AA30:AU30', 'Fin_Statements.AA31:AU31', 'Fin_Statements.AA32:AU32',
        'Fin_Statements.AA34:AU34', 'Fin_Statements.AA35:AU35', 'Fin_Statements.AA40:AU40',
        'Fin_Statements.AA41:AU41', 'Fin_Statements.AA42:AU42', 'Fin_Statements.AA43:AU43',
        'Fin_Statements.AA49:AU49', 'Fin_Statements.AA51:AU51', 'Fin_Statements.AA52:AU52',
        'Fin_Statements.AA53:AU53', 'Fin_Statements.AA54:AU54', 'Fin_Statements.AA55:AU55',
        'Fin_Statements.AA56:AU56', 'Fin_Statements.AA57:AU57', 'Fin_Statements.AA59:AU59',
        'Fin_Statements.AA5:AU5', 'Fin_Statements.AA62:AU62', 'Fin_Statements.AA63:AU63',
        'Fin_Statements.AA64:AU64', 'Fin_Statements.AA65:AU65', 'Fin_Statements.AA66:AU66',
        'Fin_Statements.AA67:AU67', 'Fin_Statements.AA68:AU68', 'Fin_Statements.AA6:AU6',
        'Fin_Statements.AA74:AU74', 'Fin_Statements.AA75:AU75', 'Fin_Statements.AA76:AU76',
        'Fin_Statements.AA77:AU77', 'Fin_Statements.AA7:AU7', 'Fin_Statements.AA80:AU80',
        'Fin_Statements.AA81:AU81', 'Fin_Statements.AA82:AU82', 'Fin_Statements.AA84:AU84',
        'Fin_Statements.AA85:AU85', 'Fin_Statements.AA86:AU86', 'Fin_Statements.AA87:AU87',
        'Fin_Statements.AA88:AU88', 'Fin_Statements.AA89:AU89', 'Fin_Statements.AA8:AU8',
        'Fin_Statements.AA90:AU90', 'Fin_Statements.AA91:AU91', 'Fin_Statements.AA92:AU92',
        'Fin_Statements.C105:D105', 'Fin_Statements.C106:D106', 'Fin_Statements.C10:W10',
        'Fin_Statements.C116:D116', 'Fin_Statements.C117:D117', 'Fin_Statements.C11:W11',
        'Fin_Statements.C120:D120', 'Fin_Statements.C121:D121', 'Fin_Statements.C12:W12', 'Fin_Statements.C13:W13',
        'Fin_Statements.C14:W14', 'Fin_Statements.C15:W15', 'Fin_Statements.C16:W16', 'Fin_Statements.C23:W23',
        'Fin_Statements.C28:W28', 'Fin_Statements.C30:W30', 'Fin_Statements.C31:W31', 'Fin_Statements.C32:W32',
        'Fin_Statements.C34:W34', 'Fin_Statements.C35:W35', 'Fin_Statements.C40:W40', 'Fin_Statements.C41:W41',
        'Fin_Statements.C42:W42', 'Fin_Statements.C43:W43', 'Fin_Statements.C49:W49', 'Fin_Statements.C51:W51',
        'Fin_Statements.C52:W52', 'Fin_Statements.C53:W53', 'Fin_Statements.C54:X54', 'Fin_Statements.C55:X55',
        'Fin_Statements.C56:X56', 'Fin_Statements.C57:X57', 'Fin_Statements.C59:W59', 'Fin_Statements.C5:W5',
        'Fin_Statements.C62:W62', 'Fin_Statements.C63:W63', 'Fin_Statements.C64:W64', 'Fin_Statements.C65:X65',
        'Fin_Statements.C66:X66', 'Fin_Statements.C67:X67', 'Fin_Statements.C68:X68', 'Fin_Statements.C6:W6',
        'Fin_Statements.C74:W74', 'Fin_Statements.C77:W77', 'Fin_Statements.C7:W7', 'Fin_Statements.C80:W80',
        'Fin_Statements.C81:W81', 'Fin_Statements.C82:W82', 'Fin_Statements.C85:W85', 'Fin_Statements.C86:W86',
        'Fin_Statements.C87:W87', 'Fin_Statements.C88:W88', 'Fin_Statements.C89:W89', 'Fin_Statements.C8:W8',
        'Fin_Statements.C90:W90', 'Fin_Statements.C91:W91', 'Fin_Statements.C92:W92', 'Fin_Statements.D100:M100',
        'Fin_Statements.D25:W25', 'Fin_Statements.D26:W26', 'Fin_Statements.D27:W27', 'Fin_Statements.D75:W75',
        'Fin_Statements.D76:W76', 'Fin_Statements.D84:W84', 'Fin_Statements.D98:W98', 'Fin_Statements.D99:W99',
        'OPEX.C120:J120', 'OPEX.C122:J122', 'OPEX.C124:J124', 'OPEX.H109:S109', 'OPEX.H111:S111', 'OPEX.H89:S89',
        'OPEX.H92:S92', 'OPEX.H93:S93', 'OPEX.H95:S95', 'WC_Financing.C15:E15', 'WC_Financing.C217:W217',
        'WC_Financing.C219:W219', 'WC_Financing.C221:W221', 'WC_Financing.C234:W234', 'WC_Financing.C236:W236',
        'WC_Financing.C238:W238', 'WC_Financing.C251:W251', 'WC_Financing.C253:W253', 'WC_Financing.C255:W255',
        'WC_Financing.C302:W302', 'WC_Financing.C304:W304', 'WC_Financing.C306:W306', 'WC_Financing.C319:W319',
        'WC_Financing.C321:W321', 'WC_Financing.C323:W323', 'WC_Financing.C336:W336', 'WC_Financing.C338:W338',
        'WC_Financing.C340:W340', 'WC_Financing.C47:W47', 'WC_Financing.C49:W49', 'WC_Financing.C51:W51',
        'WC_Financing.C64:W64', 'WC_Financing.C66:W66', 'WC_Financing.C68:W68', 'WC_Financing.C81:W81',
        'WC_Financing.C83:W83', 'WC_Financing.C85:W85', 'WC_Financing.D213:W213', 'WC_Financing.D214:W214',
        'WC_Financing.D215:W215', 'WC_Financing.D218:W218', 'WC_Financing.D21:M21', 'WC_Financing.D230:W230',
        'WC_Financing.D231:W231', 'WC_Financing.D232:W232', 'WC_Financing.D235:W235', 'WC_Financing.D247:W247',
        'WC_Financing.D248:W248', 'WC_Financing.D249:W249', 'WC_Financing.D24:M24', 'WC_Financing.D252:W252',
        'WC_Financing.D28:W28', 'WC_Financing.D298:W298', 'WC_Financing.D299:W299', 'WC_Financing.D29:W29',
        'WC_Financing.D300:W300', 'WC_Financing.D303:W303', 'WC_Financing.D315:W315', 'WC_Financing.D316:W316',
        'WC_Financing.D317:W317', 'WC_Financing.D31:W31', 'WC_Financing.D320:W320', 'WC_Financing.D332:W332',
        'WC_Financing.D333:W333', 'WC_Financing.D334:W334', 'WC_Financing.D337:W337', 'WC_Financing.D43:W43',
        'WC_Financing.D44:W44', 'WC_Financing.D45:W45', 'WC_Financing.D48:W48', 'WC_Financing.D60:W60',
        'WC_Financing.D61:W61', 'WC_Financing.D62:W62', 'WC_Financing.D65:W65', 'WC_Financing.D77:W77',
        'WC_Financing.D78:W78', 'WC_Financing.D79:W79', 'WC_Financing.D82:W82',
        'driver Beef_Herd_Movement.C25:AL25', 'driver Beef_Herd_Movement.C26:AL26',
        'driver Beef_Herd_Movement.C27:AL27', 'driver Beef_Herd_Movement.C28:AL28', 'driver Dashboard.C12',
        'driver Dashboard.C14', 'driver Dashboard.E40:Y40', 'driver Dashboard.E43:Y43', 'driver Dashboard.E44:Y44',
        'driver Dashboard.E45:Y45', 'driver Dashboard.E46:Y46', 'driver Dashboard.E47:Y47',
        'driver Dashboard.E48:Y48', 'driver Dashboard.E49:Y49', 'driver Dashboard.E50:Y50',
        'driver Fin_Statements.AB17:AT17', 'driver Fin_Statements.D17:V17'},
    "modeloff_2015_tax_solution": {
        # not a slip: no reading yet tests a record for nonzero (the first month a loss expires);
        # the row is all zeros here and feeds only F104, a date answer the oracle sets aside
        "Solution.M62:EN62"},
}


def test_the_workbooks_are_vendored_and_in_the_manifest():
    assert set(ORACLES) == VENDORED
    for oid in ORACLES:
        O.verify(FAMILY.source(oid))
        assert FAMILY.source(oid) in (f"{FOLDER}/{oid}.xlsx", f"{FOLDER}/{oid}.xlsm")
    assert FAMILY.source("bodmer_solar_case_study").endswith(".xlsm")


def test_inputs_are_named_mechanically_and_a_number_inside_a_formula_is_one():
    assert sheet_key("Coûts de product° Agricole") == "Couts_de_product_Agricole"
    assert sheet_key("50 CAP") == "_50_CAP"
    assert _literals("=M29*1.5") == [Fraction(3, 2)]
    assert _literals("=1-10%") == [1, Fraction(1, 10)]
    assert _literals("=SUM(C5:C9)") == []
    book = read_workbook(FAMILY.reference / FOLDER / "nour_3moissons_haraz.xlsx")
    assert book["inputs"]["Hypotheses_Nour_3Moissons__N29__n1"] == Fraction(3, 2)
    assert "VAN_Nour_Baghara__C7_W7" in book["inputs"]                 # a typed row is also a series
    assert all(isinstance(v, (Fraction, list)) for v in book["inputs"].values())


def test_a_typed_date_is_an_input_by_its_excel_serial():
    # Excel stores a date as a day count (1 = 1 Jan 1900 in the 1900 system); a model computes with it
    # (days in a period, a day-weighted rate), so the date the modeller typed is a number they typed
    book = read_workbook(FAMILY.reference / FOLDER / "modeloff_2015_more_money_please.xlsx")
    assert book["inputs"]["Inputs__L12"] == 50586                       # 30 June 2038


def test_a_cell_read_from_another_workbook_is_an_input_with_the_value_it_imported():
    # '1 CAP'!D33 is =[2]PL!D27, a workbook that was not saved with this one: nothing here can recompute
    # it, so the value the workbook imported is what it computed from
    book = read_workbook(FAMILY.reference / FOLDER / "van_10ans_previsions_3moissons.xlsx")
    assert float(book["inputs"]["_1_CAP__E33"]) == pytest.approx(218.11174)
    assert isinstance(book["inputs"]["_1_CAP__D33_J33"], list)              # an imported row is a series too
    assert book["figures"]["_1_CAP.C33"] and not [f for f in book["figures"] if f.startswith("_1_CAP.D33")]
    assert all("[" not in t for fs in book["formulas"].values() for t in fs)


def test_a_formula_cell_that_saved_no_number_is_set_aside_by_the_oracle_with_what_it_saved():
    book = read_workbook(FAMILY.reference / FOLDER / "cattle_integration_financial_model.xlsx")
    assert book["aside"] and all(r.startswith("the workbook saved no number here") for r in book["aside"].values())
    assert not set(book["aside"]) & set(book["figures"])


def test_rate_cells_keep_excels_precision_and_the_rest_double_drift():
    fig = "VAN_Nour_Baghara.C55:F55"
    _, formulas = FAMILY._load("nour_3moissons_haraz")
    assert all("IRR(" in f for f in formulas[fig])
    v = Fraction(1, 10)
    assert FAMILY._within(fig, v, v + ITERATIVE_TOL, formulas, None)[0]
    assert not FAMILY._within(fig, v, v + 2 * ITERATIVE_TOL, formulas, None)[0]
    other = "VAN_Nour_Baghara.C24:W24"
    big = Fraction(10 ** 7)
    assert FAMILY._within(other, big, big + Fraction(9, 1000), formulas, None)[0]        # 1e-9 relative
    assert not FAMILY._within(other, big, big + Fraction(11, 1000), formulas, None)[0]
    assert not FAMILY._within(other, Fraction(0), Fraction(2, 10 ** 6), formulas, None)[0]  # 1e-6 floor


def _translation_copy(tmp_path, edit):
    src = FAMILY.translations / "nour_3moissons_haraz.yaml"
    tr = yaml.safe_load(src.read_text())
    edit(tr)
    (tmp_path / "nour_3moissons_haraz.yaml").write_text(yaml.safe_dump(tr, sort_keys=False))
    return WorkbookFamily(translations=tmp_path)


def test_a_figure_neither_read_nor_set_aside_fails_and_a_whole_sheet_may_be_set_aside(tmp_path):
    fam = _translation_copy(tmp_path, lambda tr: tr["figures"].pop("VAN_Nour_Baghara.C24:W24"))
    checks, _ = fam.run("nour_3moissons_haraz")
    gone = [c for c in checks if c.name == "VAN_Nour_Baghara.C24:W24"]
    assert len(gone) == 1 and gone[0].got == "not reported" and not gone[0].ok

    def sheet(tr):
        for f in [f for f in tr["figures"] if f.startswith("Hypotheses_Nour_Haraz.")]:
            del tr["figures"][f]
        tr["not_compared"] = {"Hypotheses_Nour_Haraz": "planted: the whole sheet set aside"}
    checks, record = _translation_copy(tmp_path, sheet).run("nour_3moissons_haraz")
    assert not [c for c in checks if c.name.startswith("Hypotheses_Nour_Haraz.")]
    assert record["not_compared"] == {"Hypotheses_Nour_Haraz": "planted: the whole sheet set aside"}

    def both(tr):
        tr["not_compared"] = {"VAN_Nour_Baghara": "planted: a sheet whose figures are also read"}
    with pytest.raises(ValueError, match="read and set aside"):
        _translation_copy(tmp_path, both).run("nour_3moissons_haraz")


@pytest.mark.parametrize("oid", ORACLES)
def test_every_workbook_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    if FAMILY.translation(oid) is None:
        assert r.verdict == "blocked" and r.reason.startswith("no translation yet"), r.reason
        return
    failing = {c["name"] for c in r.checks if not c["ok"]}
    if oid in SLIPS:
        assert r.verdict == "differs" and failing == SLIPS[oid], failing
    else:
        assert r.verdict == "matches", failing
    assert r.planted_caught
