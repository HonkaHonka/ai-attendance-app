SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[Att_Course_Class](
	[iSerial] [int] IDENTITY(1,1) NOT NULL,
	[iTerm] [int] NULL,
	[sTerm] [varchar](50) NULL,
	[Campus] [varchar](50) NULL,
	[FacultyCode] [varchar](50) NULL,
	[Department] [varchar](50) NULL,
	[CourseID] [varchar](50) NULL,
	[Code] [varchar](50) NULL,
	[EquivalencyCode] [varchar](50) NULL,
	[CourseName] [nvarchar](100) NULL,
	[ClassNbr] [varchar](50) NULL,
	[ClassID] [varchar](50) NULL,
	[CombinedSectionID] [varchar](50) NULL,
	[ClassType] [varchar](50) NULL,
	[FacultyID] [varchar](50) NULL,
	[FacultyCampusID] [varchar](50) NULL,
	[FacultyName] [varchar](50) NULL,
	[SessionDays] [varchar](50) NULL,
	[StartTime] [datetime] NULL,
	[EndTime] [datetime] NULL,
	[RoomID] [varchar](50) NULL,
	[iSection] [int] NULL,
 CONSTRAINT [PK_Att_Course_Class] PRIMARY KEY CLUSTERED 
(
	[iSerial] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
) ON [PRIMARY]
GO


