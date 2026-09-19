# EXPERIMENTAL parser bridge for the first physical `.qm` specimen. Not production.
#
# Resolves an anchor inside a PowerShell file with PowerShell's OWN parser and
# emits AST-derived facts as JSON on stdout. Textual brace counting was the
# first draft and was rejected: the first `.qm` semantic link must not depend on
# a parser weaker than the language it claims to observe.
#
# Emits only FACTS. It asserts nothing — every assertion lives in the .qm
# specimen, and scripts/qm_link_check.py is what compares the two.

param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Function)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path $Path).Path, [ref]$tokens, [ref]$errors)

if ($errors.Count -gt 0) {
    @{ parse_ok = $false; parse_errors = @($errors | ForEach-Object { $_.Message }) } |
        ConvertTo-Json -Depth 6
    exit 0
}

$fn = $ast.FindAll({
        $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $args[0].Name -eq $Function
    }, $true) | Select-Object -First 1

if ($null -eq $fn) {
    @{ parse_ok = $true; anchor_found = $false } | ConvertTo-Json -Depth 6
    exit 0
}

# --- the control surface: what the function RETURNS -------------------------
$returns = @($fn.Body.FindAll({
            $args[0] -is [System.Management.Automation.Language.ReturnStatementAst]
        }, $true) | ForEach-Object { $_.Pipeline.Extent.Text })

# --- the partition: the predicate assigned to $ok ---------------------------
$okAssign = $fn.Body.FindAll({
        $args[0] -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $args[0].Left.Extent.Text -eq '$ok'
    }, $true) | Select-Object -First 1

$predicate = $null
if ($null -ne $okAssign) {
    $bin = $okAssign.Right.FindAll({
            $args[0] -is [System.Management.Automation.Language.BinaryExpressionAst]
        }, $true)
    $predicate = @{
        rhs_text          = $okAssign.Right.Extent.Text
        binary_node_count = $bin.Count
        operator          = if ($bin.Count -ge 1) { "$($bin[0].Operator)" } else { $null }
        left              = if ($bin.Count -ge 1) { $bin[0].Left.Extent.Text } else { $null }
        right             = if ($bin.Count -ge 1) { $bin[0].Right.Extent.Text } else { $null }
    }
}

# --- the non-control surface: does the exact integer reach the console? ------
$hostWritesCarryingCode = @($fn.Body.FindAll({
            $args[0] -is [System.Management.Automation.Language.CommandAst] -and
            $args[0].GetCommandName() -eq 'Write-Host'
        }, $true) | Where-Object {
        $_.FindAll({
                $args[0] -is [System.Management.Automation.Language.VariableExpressionAst] -and
                $args[0].VariablePath.UserPath -eq 'code'
            }, $true).Count -gt 0
    }).Count

# --- call sites of the anchor, and which of them pass the subject -----------
$calls = @($ast.FindAll({
            $args[0] -is [System.Management.Automation.Language.CommandAst] -and
            $args[0].GetCommandName() -eq $Function
        }, $true))

# A single-element `@('x')` parses as an ArrayExpressionAst with NO
# ArrayLiteralAst inside it — a comma is what makes a literal. The first draft
# reported $null for those call sites; falling back to the first string
# constant covers them, so every call site resolves rather than most of them.
$firstArgs = @($calls | ForEach-Object {
        $arr = $_.FindAll({
                $args[0] -is [System.Management.Automation.Language.ArrayLiteralAst]
            }, $true) | Select-Object -First 1
        if ($null -ne $arr -and $arr.Elements.Count -gt 0) {
            "$($arr.Elements[0].Value)"
        }
        else {
            $lit = $_.FindAll({
                    $args[0] -is [System.Management.Automation.Language.StringConstantExpressionAst]
                }, $true) | Where-Object { $_.Value -ne $Function } | Select-Object -Skip 1 -First 1
            if ($null -ne $lit) { "$($lit.Value)" } else { $null }
        }
    })

@{
    parse_ok                    = $true
    anchor_found                = $true
    anchor_first_line           = $fn.Extent.StartLineNumber
    anchor_last_line            = $fn.Extent.EndLineNumber
    returns                     = $returns
    ok_predicate                = $predicate
    host_writes_carrying_code   = $hostWritesCarryingCode
    call_sites_total            = $calls.Count
    call_site_first_argv        = $firstArgs
} | ConvertTo-Json -Depth 6
