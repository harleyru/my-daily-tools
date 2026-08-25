on run
	tell application "Calendar"
		tell calendar "Agent"
			set startDate to current date
			set year of startDate to 2026
			set month of startDate to 8
			set day of startDate to 19
			set hours of startDate to 14
			set minutes of startDate to 0
			set seconds of startDate to 0
			set endDate to startDate + (1 * hours)
			make new event with properties {summary:"Newport 工程师看气浮", start date:startDate, end date:endDate}
		end tell
	end tell
end run
